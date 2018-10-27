source activate TFG
python ../mrcnn/train_coco_fcn.py    \
    --epochs               15   \
    --steps_in_epoch       12   \
    --last_epoch            8   \
    --batch_size            2   \
    --lr                 0.01   \
    --val_steps             4   \
    --mrcnn_logs_dir   train_mrcnn_coco \
    --fcn_logs_dir     train_fcn_coco \
    --model              last   \
    --fcn_model          last   \
    --opt             adagrad   \
    --sysout             file
source deactivate
##  --new_log_folder  \
