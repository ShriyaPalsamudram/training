# coding=utf-8
# Copyright (c) 2020, NVIDIA CORPORATION.  All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""GPT-2 model."""
from functools import partial

import torch

from megatron import get_args
from megatron import mpu
from megatron.core.dist_checkpointing.utils import add_prefix_for_sharding
from .module import MegatronModule

from .enums import AttnMaskType, LayerType
from .language_model import parallel_lm_logits
from .language_model import get_language_model
from .utils import init_method_normal, make_tp_sharded_tensor_for_checkpoint
from .utils import scaled_init_method_normal


def post_language_model_processing(lm_output, labels, logit_weights,
                                   parallel_output,
                                   fp16_lm_cross_entropy):

    # Output. Format [s b h]
    output = parallel_lm_logits(
        lm_output,
        logit_weights,
        parallel_output)

    if labels is None:
        # [s b h] => [b s h]
        return output.transpose(0,1).contiguous()
    else:
        # [b s] => [s b]
        labels = labels.transpose(0,1).contiguous()
        if fp16_lm_cross_entropy:
            assert output.dtype == torch.half
            loss = mpu.vocab_parallel_cross_entropy(output, labels)
        else:
            loss = mpu.vocab_parallel_cross_entropy(output.float(), labels)
        
        # [s b] => [b, s]
        loss = loss.transpose(0,1).contiguous()
        return loss


class GPTModel(MegatronModule):
    """GPT-2 Language model."""

    def __init__(self,
                 num_tokentypes=0,
                 parallel_output=True,
                 pre_process=True,
                 post_process=True):
        super(GPTModel, self).__init__()
        args = get_args()

        self.parallel_output = parallel_output
        self.pre_process = pre_process
        self.post_process = post_process
        self.fp16_lm_cross_entropy = args.fp16_lm_cross_entropy
        if args.no_scaled_init:
            scaled_init_method = init_method_normal(args.init_method_std)
        else:
            scaled_init_method = scaled_init_method_normal(args.init_method_std, args.num_layers)


        self.language_model, self._language_model_key = get_language_model(
            num_tokentypes=num_tokentypes,
            add_pooler=False,
            encoder_attn_mask_type=AttnMaskType.causal,
            init_method=init_method_normal(args.init_method_std),
            scaled_init_method=scaled_init_method,
            pre_process=self.pre_process,
            post_process=self.post_process)

        self.initialize_word_embeddings(init_method_normal)

    def set_input_tensor(self, input_tensor):
        """See megatron.model.transformer.set_input_tensor()"""
        self.language_model.set_input_tensor(input_tensor)

    def forward(self, input_ids, position_ids, attention_mask, labels=None,
                tokentype_ids=None, inference_params=None):

        lm_output = self.language_model(
            input_ids,
            position_ids,
            attention_mask,
            inference_params=inference_params)

        if self.post_process:
            return post_language_model_processing(
                lm_output, labels,
                self.word_embeddings_weight(),
                self.parallel_output,
                self.fp16_lm_cross_entropy)
        else:
            return lm_output

    def state_dict_for_save_checkpoint(self, destination=None, prefix='',
                                       keep_vars=False, unified_checkpoint=False):
        state_dict_ = {}
        state_dict_[self._language_model_key] \
            = self.language_model.state_dict_for_save_checkpoint(
                destination, prefix, keep_vars, unified_checkpoint=unified_checkpoint)
        if unified_checkpoint:
            add_prefix_for_sharding(state_dict_[self._language_model_key], self._language_model_key)
        # Save word_embeddings.
        if self.post_process and not self.pre_process:
            state_dict_[self._word_embeddings_for_head_key] \
                = self.word_embeddings.state_dict(
                    destination, prefix,keep_vars or unified_checkpoint)
            if unified_checkpoint:
                state_dict_[self._word_embeddings_for_head_key]['weight'] \
                    = make_tp_sharded_tensor_for_checkpoint(
                        state_dict_[self._word_embeddings_for_head_key]['weight'],
                        f'{self._language_model_key}.embedding.word_embeddings.weight',  # reusing the same tensor!
                        replica_id=mpu.get_data_parallel_rank() + mpu.get_data_parallel_world_size(),  # indexed twice
                        allow_shape_mismatch=True
                    )
        return state_dict_

    def load_state_dict(self, state_dict, strict=True):
        """Customized load."""

        # Load word_embeddings.
        if self.post_process and not self.pre_process:
            self.word_embeddings.load_state_dict(
                state_dict[self._word_embeddings_for_head_key], strict=strict)
        if self._language_model_key in state_dict:
            state_dict = state_dict[self._language_model_key]
        self.language_model.load_state_dict(state_dict, strict=strict)
