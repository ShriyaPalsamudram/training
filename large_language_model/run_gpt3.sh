#!/bin/bash

#SBATCH -p luna -A mlperf -t 00:20:00 --nodes=8 --exclusive --mem=0 --overcommit --ntasks-per-node=8 --job-name=mlperf-megatron:megatron

DIR=$PWD
MEGATRON_DIR="${DIR}"

LOG_DIR=$1
CHECKPOINT_DIR="${LOG_DIR}/GPT3-175B-checkpoints"
TENSORBOARD_DIR="${LOG_DIR}/GPT3-175B-tensorboard"

mkdir -p ${LOG_DIR}
mkdir -p ${CHECKPOINT_DIR}
mkdir -p ${TENSORBOARD_DIR}

# Get the data blend
. $PWD/gpt3_blend.sh

################################################################################
### Set exit duration based on variable time allocated for this specific job ###
# Query Slurm for the remaining job time left in the format [days-]hh:mm:ss
# format and pass the time (in units of minutes) to Megatron using variable
# EXIT_DURATION. The actual value passed is actually 13 minutes less for time
# to save model and extra margin. For our purposes we assume the days field
# will never be present to make parsing in bash easier. Note that setting
# EXIT_DURATION to 0 will terminate the job after 1 iteration.
timeleft=`squeue -j ${SLURM_JOBID} --noheader --format=%L`
timeleft=(`echo $timeleft | tr ':' ' '`)
EXIT_DURATION=$((timeleft[0]*60 + timeleft[1] - 15))
echo "setting exit duration to $EXIT_DURATION minutes"
################################################################################

options=" \
--exit-duration-in-mins ${EXIT_DURATION} \
--tensor-model-parallel-size 8 \
--pipeline-model-parallel-size 8 \
--num-layers 96 \
--hidden-size 12288 \
--num-attention-heads 96 \
--seq-length 2048 \
--max-position-embeddings 2048 \
--micro-batch-size 1 \
--global-batch-size 1536 \
--train-samples 364868901 \
--lr 6.0e-5 \
--min-lr 6.0e-6 \
--lr-decay-style cosine \
--log-interval 1 \
--eval-iters 50 \
--eval-interval 50 \
--data-path ${DATA_BLEND} \
--vocab-file $2/vocab.json \
--merge-file $2/merges.txt \
--save-interval 100 \
--save ${CHECKPOINT_DIR} \
--load ${CHECKPOINT_DIR} \
--split 98,2,0 \
--clip-grad 1.0 \
--weight-decay 0.1 \
--adam-beta1 0.9 \
--adam-beta2 0.95 \
--init-method-std 0.006 \
--log-params-norm \
--log-num-zeros-in-grad \
--log-validation-ppl-to-tensorboard \
--DDP-impl local \
--tensorboard-dir ${TENSORBOARD_DIR} \
--checkpoint-activations \
--seed ${RANDOM} "

run_cmd="python -u ${MEGATRON_DIR}/pretrain_gpt.py ${options}"
DATETIME=`date +'date_%y-%m-%d_time_%H-%M-%S'`

srun -l \
     --container-image "nvcr.io/nvidia/pytorch:21.12-py3" \
     --container-mounts "$PWD:$PWD,${COM_DIR}:${COM_DIR},${LOG_DIR}:${LOG_DIR},$2:$2" \
     --output=$LOG_DIR/GPT3-175B-runlog-$DATETIME.log sh -c "${run_cmd}"

set +x

