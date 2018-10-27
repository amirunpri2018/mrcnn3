"""
Mask R-CNN
Contextual Heatmap Layer for Training Mode - Version 1

Copyright (c) 2018 K.Bardool 
Licensed under the MIT License (see LICENSE for details)
Written by Kevin Bardool
"""

import os, sys, glob, random, math, datetime, itertools, json, re, logging, pprint
import numpy as np
from scipy.stats import  multivariate_normal
import tensorflow as tf
import keras.backend as KB
import keras.layers as KL
import keras.engine as KE
sys.path.append('..')
import mrcnn.utils as utils
import tensorflow.contrib.util as tfc

##-----------------------------------------------------------------------------------------------------------
##
##  09-21-2018: 
##    **  build_predictions --> build_refined_predictions
##        build_predicitions routine now applies predicted refinement (from mrcnn_bbox deltas) to the 
##        output_rois. After applying the delta, the refined output rois are clipped so they dont exceed
##        the NN image dimensions. The modified coordiantes of the bboxes are placed in pred_tensor for 
##        futher processing           
##
##    **  renamed build_mask_routine --> build_hm_bbox_score
##        better reflects the function
##
##    ** Modified build_headmap : 
##     - Added HEATMAP_SCALE_FACTOR
##  
##     - Scores are now built using dense tensors : Instead of using `gauss_heatmap` (gauss_sum) and 
##       `in_tensor`, where the process of flattening and running build_hm_bbox_score against them 
##       includes a large number of empty [0 0 0 0] bboxes (since gauss_heatmap is replicated  
##       [num_classes x num_bbox_per_class] times), the score calculation will be done on prob_grid 
##       and pt2_dense , which only include the real number of non-zero bboxes.                  
##       The scores are build using the same method (map_fn)
##       Scores are 1) sum of gaussian distribution within bounding box 
##                      2) area of bounding box in pixels (not used)
##                      3) sum of gaussian within bounding bouding box * normalized score. 
##
##-----------------------------------------------------------------------------------------------------------   
##----------------------------------------------------------------------------------------------------------- 
## build_refined_predictions 
##-----------------------------------------------------------------------------------------------------------
def build_predictions(norm_input_rois, mrcnn_class, mrcnn_bbox, config):
    '''
    Split output_rois by class id, and add class_id and class_score 
    
    output:
    -------
    
    pred_tensor:        [ Bsz, Num_Classes, Num_Rois, 7: 
                            {y1, x1, y2, x2, class_id, class_score, normalized class score}]
                        
                        y1,x1, y2,x2 are in image dimension format
    '''
    batch_size      = config.BATCH_SIZE
    num_classes     = config.NUM_CLASSES
    h, w            = config.IMAGE_SHAPE[:2]
    # num_rois        = config.TRAIN_ROIS_PER_IMAGE
    num_rois        = KB.int_shape(norm_input_rois)[1]
    scale           = tf.constant([h,w,h,w], dtype = tf.float32)
    # dup_scale       = tf.reshape(tf.tile(scale, [num_rois]),[num_rois,-1])
    dup_scale       = scale * tf.ones([batch_size, num_rois, 1], dtype = 'float32')
    det_per_class   = config.DETECTION_PER_CLASS
    
    print()
    print('  > build_predictions()')
    print('    num_rois               : ', num_rois )
    print('    norm_input_rois.shape  : ', type(norm_input_rois), KB.int_shape(norm_input_rois))
    print('    scale.shape            : ', type(scale), KB.int_shape(scale), scale.get_shape())
    print('    dup_scale.shape        : ', type(dup_scale), KB.int_shape(dup_scale), dup_scale.get_shape())
    print()
    print('    mrcnn_class shape      : ', KB.int_shape(mrcnn_class))
    print('    mrcnn_bbox.shape       : ', KB.int_shape(mrcnn_bbox), mrcnn_bbox.shape )
    print('    config image shape     : ', config.IMAGE_SHAPE, 'h:',h,'w:',w)

    #---------------------------------------------------------------------------
    # Build a meshgrid for image id and bbox to use in gathering of bbox delta information 
    #---------------------------------------------------------------------------
    batch_grid, bbox_grid = tf.meshgrid( tf.range(batch_size, dtype=tf.int32),
                                         tf.range(num_rois, dtype=tf.int32), indexing = 'ij' )

    #------------------------------------------------------------------------------------
    # use the argmaxof each row to determine the dominating (predicted) class
    #------------------------------------------------------------------------------------
    pred_classes     = tf.argmax( mrcnn_class,axis=-1,output_type = tf.int32)
    pred_classes_exp = tf.to_float(tf.expand_dims(pred_classes ,axis=-1))    
    #     print('    pred_classes : ', pred_classes.shape)
    #     print(pred_classes.eval())
    #     print('    pred_scores  : ', pred_scores.shape ,'\n', pred_scores.eval())
    #     print('    pred_classes_exp : ', pred_classes_exp.shape)
    
    gather_ind   = tf.stack([batch_grid , bbox_grid, pred_classes],axis = -1)
    pred_scores  = tf.gather_nd(mrcnn_class, gather_ind)
    pred_deltas  = tf.gather_nd(mrcnn_bbox , gather_ind)

    ##------------------------------------------------------------------------------------
    ## apply delta refinements to the  rois,  based on deltas provided by the mrcnn head 
    ##------------------------------------------------------------------------------------
    pred_deltas  = tf.multiply(pred_deltas, config.BBOX_STD_DEV, name = 'pred_deltas')
    input_rois   = tf.multiply(norm_input_rois , dup_scale )

    ## compute "refined rois"  utils.apply_box_deltas_tf(input_rois, pred_deltas)
    refined_rois   = utils.apply_box_deltas_tf(input_rois, pred_deltas)

    ##   Clip boxes to image window    
    window = tf.constant([[0,0,h,w]], dtype = tf.float32)
    refined_rois  = utils.clip_to_window_tf( window, refined_rois)
    
    print('    refined rois clipped   : ', refined_rois.shape)
    print('    input_rois.shape       : ', KB.int_shape(input_rois), input_rois.get_shape())
    print('    refined_rois.shape     : ', KB.int_shape(refined_rois), refined_rois.get_shape())
    # print('    mrcnn_class : ', mrcnn_class.shape, mrcnn_class)
    # print('    gather_ind  : ', gather_ind.shape, gather_ind)
    # print('    pred_scores : ', pred_scores.shape )
    # print('    pred_deltas : ', pred_deltas.shape )   
    # print('    input_rois : ', input_rois.shape, input_rois)
    # print('    refined rois: ', refined_rois.shape, refined_rois)
        
    ##------------------------------------------------------------------------------------
    ##  Build Pred_Scatter: tensor of bounding boxes by Image / Class
    ##------------------------------------------------------------------------------------
    ## sequence id is used to preserve the order of rois as passed to this routine
    ##  This may be important in the post matching process but for now it's not being used.
    ## 22-09-18 : We need to use this sequence as the sort process based on score will cause
    ##            mismatch between the bboxes from output_rois and roi_gt_bboxes
    ##------------------------------------------------------------------------------------
    sequence = tf.ones_like(pred_classes, dtype = tf.int32) * (bbox_grid[...,::-1] + 1) 
    sequence = tf.to_float(tf.expand_dims(sequence, axis = -1))   
    print('    shape of sequence      : ', sequence.shape)
    pred_array  = tf.concat([ refined_rois, pred_classes_exp , tf.expand_dims(pred_scores, axis = -1), sequence],
                            axis=-1, name = 'pred_array')
     
    scatter_ind = tf.stack([batch_grid , pred_classes, bbox_grid],axis = -1)
    pred_scatt  = tf.scatter_nd(scatter_ind, pred_array, [batch_size, num_classes, num_rois, pred_array.shape[-1]])
    print('    pred_array             : ', pred_array.shape)  
    print('    scatter_ind            : ', type(scatter_ind), 'shape', scatter_ind.shape)
    print('    pred_scatter           : ', pred_scatt.get_shape())
    
    ##--------------------------------------------------------------------------------------------
    ##  Apply a per class score normalization using the score column (COLUMN 5)
    ##  
    ##--------------------------------------------------------------------------------------------
    normalizer   = tf.reduce_max(pred_scatt[...,5], axis = -1, keepdims=True)
    normalizer   = tf.where(normalizer < 1.0e-15,  tf.ones_like(normalizer), normalizer)
    norm_score   = tf.expand_dims(pred_scatt[...,5]/normalizer, axis = -1)
    pred_scatt   = tf.concat([pred_scatt, norm_score],axis = -1)   
    print('    - Add normalized score --\n')
    print('    normalizer             : ', normalizer.shape)  
    print('    norm_score             : ', norm_score.shape)
    print('    pred_scatter           : ', pred_scatt.get_shape())
    
    ##------------------------------------------------------------------------------------
    ## Sort pred_scatt to push valid bounding boxes to top for each class dimension
    ##
    ## 22-09-2018: sort is now based on sequence which was added as last column
    ##             (previously sort was on bbox scores)
    ##------------------------------------------------------------------------------------
    _, sort_inds = tf.nn.top_k(pred_scatt[...,6], k=pred_scatt.shape[2])
    
    # build indexes to gather rows from pred_scatter based on sort order    
    class_grid, batch_grid, roi_grid = tf.meshgrid(tf.range(num_classes),tf.range(batch_size), tf.range(num_rois))
    roi_grid_exp = tf.to_float(tf.expand_dims(roi_grid, axis = -1))
    
    gather_inds  = tf.stack([batch_grid , class_grid, sort_inds],axis = -1)
    pred_tensor  = tf.gather_nd(pred_scatt, gather_inds[...,:det_per_class,:], name = 'pred_tensor')    

    print('    sort_inds              : ', type(sort_inds)   , ' shape ', sort_inds.shape)
    print('    class_grid             : ', type(class_grid)  , ' shape ', class_grid.get_shape())
    print('    batch_grid             : ', type(batch_grid)  , ' shape ', batch_grid.get_shape())
    print('    roi_grid shape         : ', type(roi_grid)    , ' shape ', roi_grid.get_shape()) 
    print('    roi_grid_exp           : ', type(roi_grid_exp), ' shape ', roi_grid_exp.get_shape())
    print('    gather_inds            : ', type(gather_inds) , ' shape ', gather_inds.get_shape())
    print('    pred_tensor            : ', pred_tensor.get_shape())

    return  pred_tensor
    
    
##-----------------------------------------------------------------------------------------------------------
##  build_heatmap : Build gaussian heatmaps using pred_tensor
##-----------------------------------------------------------------------------------------------------------  
##  v2: in this version, 
##      For heatmap generation, prob_grid is passed through "clip_heatmap" which clips the gaussian distribution
##      based on Cx, Cy and the Covar parms for each bounding box. This prob_grid_clipped is then passed on to 
##      next steps (per-class normalization, 
##      "build_hm_score" function which calculates scores is applied to "prob_grid", 
##       
## Inputs:
##      in_tensor - [BATCH_SIZE, NUM_CLASSES, DETECTIONS_PER_CLASS, 8]
##                  per-class tensor of bounding boxes (Y1,X1,Y2,X2), class_ids, mrcnn_predicted scores, 
##                  sequence_id, and per-class normalized scores       
##      config    - Model configuration object
##
## Outputs:
##
##   gauss_heatmap_norm
##   gauss_scores - [BATCH_SIZE, NUM_CLASSES, DETECTIONS_PER_CLASS, 11]
##                  same as in_tensor, adding :
##                  - sum of heatmap in masked area
##                  - area of bounding box in pixes
##                  - (sum of heatmap in masked area) * (bbox per-class normalized score from in_tensor)
##------------------------------------------------------------------------------------------------------------
def build_heatmap(in_tensor, config, names = None):
  
    num_detections  = config.DETECTION_MAX_INSTANCES
    img_h, img_w    = config.IMAGE_SHAPE[:2]
    batch_size      = config.BATCH_SIZE
    num_classes     = config.NUM_CLASSES  
    heatmap_scale   = config.HEATMAP_SCALE_FACTOR
    grid_h, grid_w  = config.IMAGE_SHAPE[:2] // heatmap_scale    
    # rois per image is determined by size of input tensor 
    #   detection mode:   config.TRAIN_ROIS_PER_IMAGE 
    #   ground_truth  :   config.DETECTION_MAX_INSTANCES
    #   strt_cls        = 0 if rois_per_image == 32 else 1
    # rois_per_image  = config.DETECTION_PER_CLASS
    rois_per_image  = (in_tensor.shape)[2]  

    print('\n ')
    print('  > build_heatmap() for ', names )
    print('    in_tensor shape        : ', in_tensor.shape)       
    print('    num bboxes per class   : ', rois_per_image )
    print('    heatmap scale        : ', heatmap_scale, 'Dimensions:  w:', grid_w,' h:', grid_h)
    
    ##-----------------------------------------------------------------------------    
    ## Stack non_zero bboxes from in_tensor into pt2_dense 
    ##-----------------------------------------------------------------------------
    # pt2_ind shape is [?, 3]. 
    #    pt2_ind[0] corresponds to image_index 
    #    pt2_ind[1] corresponds to class_index 
    #    pt2_ind[2] corresponds to roi row_index 
    # pt2_dense shape is [?, 7]
    #    pt2_dense[0:3]  roi coordinates 
    #    pt2_dense[4]    is class id 
    #    pt2_dense[5]    is score from mrcnn    
    #    pt2_dense[6]    is bbox sequence id    
    #    pt2_dense[7]    is normalized score (per class)    
    #-----------------------------------------------------------------------------
    pt2_sum = tf.reduce_sum(tf.abs(in_tensor[:,:,:,:4]), axis=-1)
    pt2_ind = tf.where(pt2_sum > 0)
    pt2_dense = tf.gather_nd( in_tensor, pt2_ind)

    print('    pt2_sum shape  : ', pt2_sum.shape)
    print('    pt2_ind shape  : ', pt2_ind.shape)
    print('    pt2_dense shape: ', pt2_dense.get_shape())

    ##-----------------------------------------------------------------------------
    ## Build mesh-grid to hold pixel coordinates  
    ##-----------------------------------------------------------------------------
    X = tf.range(grid_w, dtype=tf.int32)
    Y = tf.range(grid_h, dtype=tf.int32)
    X, Y = tf.meshgrid(X, Y)

    # duplicate (repeat) X and Y into a  batch_size x rois_per_image tensor
    print('    X/Y shapes :',  X.get_shape(), Y.get_shape())
    ones = tf.ones([tf.shape(pt2_dense)[0] , 1, 1], dtype = tf.int32)
    rep_X = ones * X
    rep_Y = ones * Y 
    print('    Ones:    ', ones.shape)                
    print('    ones_exp * X', ones.shape, '*', X.shape, '= ',rep_X.shape)
    print('    ones_exp * Y', ones.shape, '*', Y.shape, '= ',rep_Y.shape)

    # # stack the X and Y grids 
    pos_grid = tf.to_float(tf.stack([rep_X,rep_Y], axis = -1))
    print('    pos_grid before transpse : ', pos_grid.get_shape())
    pos_grid = tf.transpose(pos_grid,[1,2,0,3])
    print('    pos_grid after transpose : ', pos_grid.get_shape())    

    ##-----------------------------------------------------------------------------
    ##  Build mean and convariance tensors for Multivariate Normal Distribution 
    ##-----------------------------------------------------------------------------
    pt2_dense_scaled = pt2_dense[:,:4]/heatmap_scale
    width  = pt2_dense_scaled[:,3] - pt2_dense_scaled[:,1]      # x2 - x1
    height = pt2_dense_scaled[:,2] - pt2_dense_scaled[:,0]
    cx     = pt2_dense_scaled[:,1] + ( width  / 2.0)
    cy     = pt2_dense_scaled[:,0] + ( height / 2.0)
    means  = tf.stack((cx,cy),axis = -1)
    covar  = tf.stack((width * 0.5 , height * 0.5), axis = -1)
    covar  = tf.sqrt(covar)

    ##-----------------------------------------------------------------------------
    ##  Compute Normal Distribution for bounding boxes
    ##-----------------------------------------------------------------------------    
    tfd = tf.contrib.distributions
    mvn = tfd.MultivariateNormalDiag(loc = means,  scale_diag = covar)
    prob_grid = mvn.prob(pos_grid)
    print('    >> input to MVN.PROB: pos_grid (meshgrid) shape: ', pos_grid.shape)
    print('     Prob_grid shape from mvn.probe: ',prob_grid.shape)
    prob_grid = tf.transpose(prob_grid,[2,0,1])
    print('     Prob_grid shape after tanspose: ',prob_grid.shape)    
    print('    << output probabilities shape  : ' , prob_grid.shape)

    #--------------------------------------------------------------------------------
    # Kill distributions of NaN boxes (resulting from bboxes with height/width of zero
    # which cause singular sigma cov matrices
    #--------------------------------------------------------------------------------
    # prob_grid = tf.where(tf.is_nan(prob_grid),  tf.zeros_like(prob_grid), prob_grid)

    ##---------------------------------------------------------------------------------------------
    ## (NEW STEP) Clip heatmap to region surrounding Cy,Cx and Covar X, Y 
    ##            Similar ro what is being done for gt_heatmap in CHMLayerTarget 
    ## added 16-10-2018 to test functionality 
    ##---------------------------------------------------------------------------------------------    
    prob_grid_clipped = tf.map_fn(clip_heatmap, [prob_grid, cy,cx, covar], 
                                 dtype = tf.float32, swap_memory = True)         
    ##---------------------------------------------------------------------------------------------
    ## (1) apply normalization per bbox heatmap instance
    ##---------------------------------------------------------------------------------------------
    print('\n    normalization ------------------------------------------------------')   
    normalizer = tf.reduce_max(prob_grid_clipped, axis=[-2,-1], keepdims = True)
    normalizer = tf.where(normalizer < 1.0e-15,  tf.ones_like(normalizer), normalizer)
    print('    normalizer     : ', normalizer.shape) 
    prob_grid_norm_scaled = prob_grid_clipped / normalizer

    ##---------------------------------------------------------------------------------------------
    ## (2) multiply normalized heatmap by normalized score in in_tensor/ (pt2_dense column 7)
    ##     broadcasting : https://stackoverflow.com/questions/49705831/automatic-broadcasting-in-tensorflow
    ##---------------------------------------------------------------------------------------------    
    prob_grid_norm_scaled = tf.transpose(tf.transpose(prob_grid_norm_scaled) * pt2_dense[:,7])
    print('    prob_grid_norm_scaled : ', prob_grid_norm_scaled.shape)
   
    ##-------------------------------------------------------------------------------------
    ## (3) scatter out the probability distributions based on class 
    ##-------------------------------------------------------------------------------------
    print('\n    Scatter out the probability distributions based on class --------------') 
    gauss_scatt   = tf.scatter_nd(pt2_ind, prob_grid_norm_scaled, 
                                  [batch_size, num_classes, rois_per_image, grid_w, grid_h], 
                                  name = 'gauss_scatter')
    print('    pt2_ind shape   : ', pt2_ind.shape)  
    print('    prob_grid shape : ', prob_grid.shape)  
    print('    gauss_scatt     : ', gauss_scatt.shape)   # batch_sz , num_classes, num_rois, image_h, image_w

    ##-------------------------------------------------------------------------------------
    ## (4) SUM : Reduce and sum up gauss_scattered by class  
    ##-------------------------------------------------------------------------------------
    print('\n    Reduce sum based on class ---------------------------------------------')         
    gauss_heatmap = tf.reduce_sum(gauss_scatt, axis=2, name='pred_heatmap2')
    #--------------------------------------------------------------------------------------
    # force small sums to zero - for now (09-11-18) commented out but could reintroduce based on test results
    # gauss_heatmap = tf.where(gauss_heatmap < 1e-12, gauss_heatmap, tf.zeros_like(gauss_heatmap), name='Where1')
    #--------------------------------------------------------------------------------------
    print('    gaussian_heatmap : ', gauss_heatmap.get_shape(), 'Keras tensor ', KB.is_keras_tensor(gauss_heatmap) )      
    
    ##---------------------------------------------------------------------------------------------
    ## (5) heatmap normalization
    ##     normalizer is set to one when the max of class is zero     
    ##     this prevents elements of gauss_heatmap_norm computing to nan
    ##---------------------------------------------------------------------------------------------
    print('\n    normalization ------------------------------------------------------')   
    normalizer = tf.reduce_max(gauss_heatmap, axis=[-2,-1], keepdims = True)
    normalizer = tf.where(normalizer < 1.0e-15,  tf.ones_like(normalizer), normalizer)
    gauss_heatmap_norm = gauss_heatmap / normalizer
    print('    normalizer shape : ', normalizer.shape)   
    print('    gauss norm       : ', gauss_heatmap_norm.shape   ,' Keras tensor ', KB.is_keras_tensor(gauss_heatmap_norm) )
    
    ##--------------------------------------------------------------------------------------------
    ## (6) Transpose heatmap to shape required for FCN
    ##--------------------------------------------------------------------------------------------
    # gauss_heatmap  = tf.transpose(gauss_heatmap,[0,2,3,1], name = names[0])
    # print('    gauss_heatmap : ', gauss_heatmap.shape,' Keras tensor ', KB.is_keras_tensor(gauss_heatmap))
    
    gauss_heatmap_norm = tf.transpose(gauss_heatmap_norm,[0,2,3,1], name = names[0]+'_norm')
    print('    gauss_heatmap_norm : ', gauss_heatmap_norm.shape,' Keras tensor ', KB.is_keras_tensor(gauss_heatmap_norm) )
    
    
    ##--------------------------------------------------------------------------------------------
    ## (7) Generate scores using prob_grid and pt2_dense - NEW METHOD
    ##     added 09-21-2018
    ##--------------------------------------------------------------------------------------------
    scores_from_sum2 = tf.map_fn(build_hm_score, [prob_grid, pt2_dense_scaled, pt2_dense[:,7]], 
                                 dtype = tf.float32, swap_memory = True)
    scores_scattered = tf.scatter_nd(pt2_ind, scores_from_sum2, 
                                     [batch_size, num_classes, rois_per_image, 3], name = 'scores_scattered')
    gauss_scores = tf.concat([in_tensor, scores_scattered], axis = -1,name = names[0]+'_scores')
    print('    scores_scattered shape : ', scores_scattered.shape) 
    print('    gauss_scores           : ', gauss_scores.shape, ' Name:   ', gauss_scores.name)
    print('    gauss_scores  (FINAL)  : ', gauss_scores.shape, ' Keras tensor ', KB.is_keras_tensor(gauss_scores) )      
    print('    complete')

    return   gauss_heatmap_norm, gauss_scores  

    
##-----------------------------------------------------------------------------------------------------------
## Build Mask and Score 
##----------------------------------------------------------------------------------------------------------- 
def build_hm_score(input_list):
    '''
    Inputs:
    -----------
        heatmap_tensor :    [ image height, image width ]
        input_row      :    [y1, x1, y2, x2] in absolute (non-normalized) scale

    Returns
    -----------
        gaussian_sum :      sum of gaussian heatmap vlaues over the area covered by the bounding box
        bbox_area    :      bounding box area (in pixels)
        weighted_sum :      gaussian_sum * bbox_score
    '''
    heatmap_tensor, input_bbox, input_norm_score = input_list
    
    with tf.variable_scope('mask_routine'):
        y_extent     = tf.range(input_bbox[0], input_bbox[2])
        x_extent     = tf.range(input_bbox[1], input_bbox[3])
        Y,X          = tf.meshgrid(y_extent, x_extent)
        bbox_mask    = tf.stack([Y,X],axis=2)        
        mask_indices = tf.reshape(bbox_mask,[-1,2])
        mask_indices = tf.to_int32(mask_indices)
        mask_size    = tf.shape(mask_indices)[0]
        mask_updates = tf.ones([mask_size], dtype = tf.float32)    
        mask         = tf.scatter_nd(mask_indices, mask_updates, tf.shape(heatmap_tensor))
        # mask_sum    =  tf.reduce_sum(mask)
        heatmap_tensor = tf.multiply(heatmap_tensor, mask, name = 'mask_applied')
        bbox_area    = tf.to_float((input_bbox[2]-input_bbox[0]) * (input_bbox[3]-input_bbox[1]))
        gaussian_sum = tf.reduce_sum(heatmap_tensor)

        # Multiply gaussian_sum by score to obtain weighted sum    
        # weighted_sum = gaussian_sum * input_row[5]

        # Replaced lines above with following lines 21-09-2018
        # Multiply gaussian_sum by normalized score to obtain weighted_norm_sum 
        weighted_norm_sum = gaussian_sum * input_norm_score    # input_list[7]

    return tf.stack([gaussian_sum, bbox_area, weighted_norm_sum], axis = -1)


##-----------------------------------------------------------------------------------------------------------
## Clip Heatmap
##      Clips heatmap to a predefined vicinity (+/- 5 pixels of cy,cx)
##----------------------------------------------------------------------------------------------------------- 
def clip_heatmap(input_list):
    '''
    Inputs:
    -----------
        heatmap_tensor :    [ image height, image width ]
        cy,cx, covar   :   
        
    Returns
    -----------
        Clipped heatmap tensor 
    '''
    heatmap_tensor, cy, cx, covar = input_list

    with tf.variable_scope('mask_routine'):
        start_y      = tf.maximum(cy-covar[1],0)
        end_y        = tf.minimum(cy+covar[1], KB.int_shape(heatmap_tensor)[0])
        start_x      = tf.maximum(cx-covar[0],0)
        end_x        = tf.minimum(cx+covar[0], KB.int_shape(heatmap_tensor)[1])
        y_extent     = tf.range(start_y, end_y)
        x_extent     = tf.range(start_x, end_x)
        Y,X          = tf.meshgrid(y_extent, x_extent)
        mask_indices = tf.stack([Y,X],axis=2)        
        mask_indices = tf.reshape(mask_indices,[-1,2])
        mask_indices = tf.to_int32(mask_indices)
        mask_size    = tf.shape(mask_indices)[0]
        mask_updates = tf.ones([mask_size], dtype = tf.float32)    
        mask         = tf.scatter_nd(mask_indices, mask_updates, tf.shape(heatmap_tensor))
        # mask_sum    =  tf.reduce_sum(mask)
        heatmap_tensor = tf.multiply(heatmap_tensor, mask, name = 'mask_applied')

    return  heatmap_tensor

    
##------------------------------------------------------------------------------------------------------------
##
##------------------------------------------------------------------------------------------------------------     
class CHMLayer(KE.Layer):
    '''
    Contextual Heatmap Layer - Training Mode
    Receives the bboxes, their repsective classification and roi_outputs and 
    builds the per_class tensor

    Returns:
    -------
    Returns the following tensors:

    pred_tensor :       [batch, NUM_CLASSES, TRAIN_ROIS_PER_IMAGE, 
                                             (index, class_prob, y1, x1, y2, x2, class_id, old_idx)]
                                in normalized coordinates   
    pred_cls_cnt:       [batch, NUM_CLASSES] 
    
    gt_tensor:          [batch, NUM_CLASSES, DETECTION_MAX_INSTANCES, 
                                                (index, class_prob, y1, x1, y2, x2, class_id, old_idx)]
    gt_cls_cnt:         [batch, NUM_CLASSES]
    
    Note: Returned arrays might be zero padded if not enough target ROIs.
    
    '''

    def __init__(self, config=None, **kwargs):
        super().__init__(**kwargs)
        print('--------------------------------')
        print('>>>  CHM Layer  ')
        print('--------------------------------')
        self.config = config

        
    def call(self, inputs):

        # mrcnn_class , mrcnn_bbox,  output_rois, tgt_class_ids, tgt_bboxes = inputs
        mrcnn_class , mrcnn_bbox,  output_rois  = inputs
        print('  > CHMLayer Call() ', len(inputs))
        print('    mrcnn_class.shape    :',   mrcnn_class.shape, KB.int_shape(  mrcnn_class ))
        print('    mrcnn_bbox.shape     :',    mrcnn_bbox.shape, KB.int_shape(   mrcnn_bbox )) 
        print('    output_rois.shape    :',   output_rois.shape, KB.int_shape(  output_rois )) 
        # print('    tgt_class_ids.shape  :', tgt_class_ids.shape, KB.int_shape(tgt_class_ids )) 
        # print('    tgt_bboxes.shape     :',    tgt_bboxes.shape, KB.int_shape(   tgt_bboxes )) 
         
        pred_tensor   = build_predictions(output_rois, mrcnn_class, mrcnn_bbox, self.config)
        pr_hm_norm, pr_hm_scores  = build_heatmap(pred_tensor, self.config, names = ['pred_heatmap'])
        # pred_cls_cnt = KL.Lambda(lambda x: tf.count_nonzero(x[:,:,:,-1],axis = -1), name = 'pred_cls_count')(pred_tensor)        

        # gt_tensor     = build_ground_truth (tgt_class_ids,  tgt_bboxes, self.config)  
        # gt_hm_norm, gt_hm_scores  = build_heatmap(gt_tensor, self.config, names = ['gt_heatmap'])
        # gt_cls_cnt   = KL.Lambda(lambda x: tf.count_nonzero(x[:,:,:,-1],axis = -1), name = 'gt_cls_count')(gt_tensor)

        print()
        print('    pred_refined_heatmap        : ', pr_hm_norm.shape   , 'Keras tensor ', KB.is_keras_tensor(pr_hm_norm))
        print('    pred_refnined_heatmap_scores: ', pr_hm_scores.shape , 'Keras tensor ', KB.is_keras_tensor(pr_hm_scores))
        # print('    gt_heatmap                  : ', gt_hm_norm.shape   , 'Keras tensor ', KB.is_keras_tensor(gt_hm_norm))
        # print('    gt_heatmap_scores           : ', gt_hm_scores.shape , 'Keras tensor ', KB.is_keras_tensor(gt_hm_scores))
        print('    complete')
        
        return [  pr_hm_norm, pr_hm_scores, pred_tensor ]
                  # gt_hm_norm  ,gt_hm_scores, gt_tensor]

         
    def compute_output_shape(self, input_shape):
        # may need to change dimensions of first return from IMAGE_SHAPE to MAX_DIM
        return [
                 (None, self.config.IMAGE_SHAPE[0], self.config.IMAGE_SHAPE[1], self.config.NUM_CLASSES)  # pred_heatmap_norm
              ,  (None, self.config.NUM_CLASSES   , self.config.DETECTION_PER_CLASS ,11)                  # pred_heatmap_scores 
              ,  (None, self.config.NUM_CLASSES   , self.config.DETECTION_PER_CLASS ,8)                   # pred_tensor               
              ]
              
              
              
              # ,  (None, self.config.IMAGE_SHAPE[0], self.config.IMAGE_SHAPE[1], self.config.NUM_CLASSES)  # gt_heatmap_norm
              # ,  (None, self.config.NUM_CLASSES   , self.config.DETECTION_PER_CLASS ,12)                  # gt_heatmap+scores   

              # ----extra stuff for now ---------------------------------------------------------------------------------------------------
              # ,  (None, self.config.NUM_CLASSES   , self.config.DETECTION_PER_CLASS ,8)                  # gt_tensor               
              
              
              # ,  (None, self.config.NUM_CLASSES   , self.config.DETECTION_PER_CLASS ,4)                  # pred_deltas 
              
              # ,  (None, self.config.NUM_CLASSES , self.config.TRAIN_ROIS_PER_IMAGE    ,10)            # pred_heatmap_scores (expanded) 
              # ,  (None, self.config.NUM_CLASSES , self.config.DETECTION_MAX_INSTANCES ,10)            # gt_heatmap+scores   (expanded) 
              # ,  (None, self.config.NUM_CLASSES , self.config.TRAIN_ROIS_PER_IMAGE    , 7)            # pred_tensor
              # ,  (None, self.config.NUM_CLASSES , self.config.DETECTION_MAX_INSTANCES , 7)            # gt_tensor   (expanded) 

              
"""    
##-----------------------------------------------------------------------------------------------------------
##  build_heatmap : Build gaussian heatmaps using pred_tensor
##  The original version of build_heatmap
##
##  v1: in this version, "build_hm_score" was is being applied to prob_grid, the raw gaussian distributions
##       which wasn't normalzied (step (1)) or multiplied by the normalized score coming from in_tensor (step(2))
##------------------------------------------------------------------------------------------------------------
def build_heatmap_original(in_tensor, config, names = None):
  
    num_detections  = config.DETECTION_MAX_INSTANCES
    img_h, img_w    = config.IMAGE_SHAPE[:2]
    batch_size      = config.BATCH_SIZE
    num_classes     = config.NUM_CLASSES  
    heatmap_scale   = config.HEATMAP_SCALE_FACTOR
    grid_h, grid_w  = config.IMAGE_SHAPE[:2] // heatmap_scale    
    # rois per image is determined by size of input tensor 
    #   detection mode:   config.TRAIN_ROIS_PER_IMAGE 
    #   ground_truth  :   config.DETECTION_MAX_INSTANCES
    #   strt_cls        = 0 if rois_per_image == 32 else 1
    # rois_per_image  = config.DETECTION_PER_CLASS
    rois_per_image  = (in_tensor.shape)[2]  

    print('\n ')
    print('  > build_heatmap() for ', names )
    print('    in_tensor shape        : ', in_tensor.shape)       
    print('    num bboxes per class   : ', rois_per_image )
    print('    heatmap scale        : ', heatmap_scale, 'Dimensions:  w:', grid_w,' h:', grid_h)
    
    ##-----------------------------------------------------------------------------    
    ## Stack non_zero bboxes from in_tensor into pt2_dense 
    ##-----------------------------------------------------------------------------
    # pt2_ind shape is [?, 3]. 
    #    pt2_ind[0] corresponds to image_index 
    #    pt2_ind[1] corresponds to class_index 
    #    pt2_ind[2] corresponds to roi row_index 
    # pt2_dense shape is [?, 7]
    #    pt2_dense[0:3]  roi coordinates 
    #    pt2_dense[4]    is class id 
    #    pt2_dense[5]    is score from mrcnn    
    #    pt2_dense[6]    is bbox sequence id    
    #    pt2_dense[7]    is normalized score (per class)    
    #-----------------------------------------------------------------------------
    pt2_sum = tf.reduce_sum(tf.abs(in_tensor[:,:,:,:4]), axis=-1)
    pt2_ind = tf.where(pt2_sum > 0)
    pt2_dense = tf.gather_nd( in_tensor, pt2_ind)

    print('    pt2_sum shape  : ', pt2_sum.shape)
    print('    pt2_ind shape  : ', pt2_ind.shape)
    print('    pt2_dense shape: ', pt2_dense.get_shape())

    ##-----------------------------------------------------------------------------
    ## Build mesh-grid to hold pixel coordinates  
    ##-----------------------------------------------------------------------------
    X = tf.range(grid_w, dtype=tf.int32)
    Y = tf.range(grid_h, dtype=tf.int32)
    X, Y = tf.meshgrid(X, Y)

    # duplicate (repeat) X and Y into a  batch_size x rois_per_image tensor
    print('    X/Y shapes :',  X.get_shape(), Y.get_shape())
    ones = tf.ones([tf.shape(pt2_dense)[0] , 1, 1], dtype = tf.int32)
    rep_X = ones * X
    rep_Y = ones * Y 
    print('    Ones:    ', ones.shape)                
    print('    ones_exp * X', ones.shape, '*', X.shape, '= ',rep_X.shape)
    print('    ones_exp * Y', ones.shape, '*', Y.shape, '= ',rep_Y.shape)

    # # stack the X and Y grids 
    pos_grid = tf.to_float(tf.stack([rep_X,rep_Y], axis = -1))
    print('    pos_grid before transpse : ', pos_grid.get_shape())
    pos_grid = tf.transpose(pos_grid,[1,2,0,3])
    print('    pos_grid after transpose : ', pos_grid.get_shape())    

    ##-----------------------------------------------------------------------------
    ##  Build mean and convariance tensors for Multivariate Normal Distribution 
    ##-----------------------------------------------------------------------------
    pt2_dense_scaled = pt2_dense[:,:4]/heatmap_scale
    width  = pt2_dense_scaled[:,3] - pt2_dense_scaled[:,1]      # x2 - x1
    height = pt2_dense_scaled[:,2] - pt2_dense_scaled[:,0]
    cx     = pt2_dense_scaled[:,1] + ( width  / 2.0)
    cy     = pt2_dense_scaled[:,0] + ( height / 2.0)
    means  = tf.stack((cx,cy),axis = -1)
    covar  = tf.stack((width * 0.5 , height * 0.5), axis = -1)
    covar  = tf.sqrt(covar)

    ##-----------------------------------------------------------------------------
    ##  Compute Normal Distribution for bounding boxes
    ##-----------------------------------------------------------------------------    
    tfd = tf.contrib.distributions
    mvn = tfd.MultivariateNormalDiag(loc = means,  scale_diag = covar)
    prob_grid = mvn.prob(pos_grid)
    print('    >> input to MVN.PROB: pos_grid (meshgrid) shape: ', pos_grid.shape)
    print('     Prob_grid shape from mvn.probe: ',prob_grid.shape)
    prob_grid = tf.transpose(prob_grid,[2,0,1])
    print('     Prob_grid shape after tanspose: ',prob_grid.shape)    
    print('    << output probabilities shape  : ' , prob_grid.shape)

    #--------------------------------------------------------------------------------
    # Kill distributions of NaN boxes (resulting from bboxes with height/width of zero
    # which cause singular sigma cov matrices
    #--------------------------------------------------------------------------------
    # prob_grid = tf.where(tf.is_nan(prob_grid),  tf.zeros_like(prob_grid), prob_grid)

    ##---------------------------------------------------------------------------------------------
    ## (1) apply normalization per bbox heatmap instance
    ##---------------------------------------------------------------------------------------------
    print('\n    normalization ------------------------------------------------------')   
    normalizer = tf.reduce_max(prob_grid, axis=[-2,-1], keepdims = True)
    normalizer = tf.where(normalizer < 1.0e-15,  tf.ones_like(normalizer), normalizer)
    print('    normalizer     : ', normalizer.shape) 
    prob_grid_norm = prob_grid / normalizer

    ##---------------------------------------------------------------------------------------------
    ## (2) multiply normalized heatmap by normalized score in in_tensor/ (pt2_dense column 7)
    ##     broadcasting : https://stackoverflow.com/questions/49705831/automatic-broadcasting-in-tensorflow
    ##---------------------------------------------------------------------------------------------    
    prob_grid_norm_scaled = tf.transpose(tf.transpose(prob_grid_norm) * pt2_dense[:,7])
    print('    prob_grid_norm_scaled : ', prob_grid_norm_scaled.shape)

    ##-------------------------------------------------------------------------------------
    ## (3) scatter out the probability distributions based on class 
    ##-------------------------------------------------------------------------------------
    print('\n    Scatter out the probability distributions based on class --------------') 
    gauss_scatt   = tf.scatter_nd(pt2_ind, prob_grid_norm_scaled, 
                                  [batch_size, num_classes, rois_per_image, grid_w, grid_h], 
                                  name = 'gauss_scatter')
    print('    pt2_ind shape   : ', pt2_ind.shape)  
    print('    prob_grid shape : ', prob_grid.shape)  
    print('    gauss_scatt     : ', gauss_scatt.shape)   # batch_sz , num_classes, num_rois, image_h, image_w

    ##-------------------------------------------------------------------------------------
    ## (4) SUM : Reduce and sum up gauss_scattered by class  
    ##-------------------------------------------------------------------------------------
    print('\n    Reduce sum based on class ---------------------------------------------')         
    gauss_heatmap = tf.reduce_sum(gauss_scatt, axis=2, name='pred_heatmap2')
    #--------------------------------------------------------------------------------------
    # force small sums to zero - for now (09-11-18) commented out but could reintroduce based on test results
    # gauss_heatmap = tf.where(gauss_heatmap < 1e-12, gauss_heatmap, tf.zeros_like(gauss_heatmap), name='Where1')
    #--------------------------------------------------------------------------------------
    print('    gaussian_heatmap : ', gauss_heatmap.get_shape(), 'Keras tensor ', KB.is_keras_tensor(gauss_heatmap) )      
    
    ##---------------------------------------------------------------------------------------------
    ## (5) per-class heatmap normalization 
    ##     normalizer is set to one when the max of class is zero     
    ##     this prevents elements of gauss_heatmap_norm computing to nan
    ##---------------------------------------------------------------------------------------------
    print('\n    normalization ------------------------------------------------------')   
    normalizer = tf.reduce_max(gauss_heatmap, axis=[-2,-1], keepdims = True)
    normalizer = tf.where(normalizer < 1.0e-15,  tf.ones_like(normalizer), normalizer)
    gauss_heatmap_norm = gauss_heatmap / normalizer
    print('    normalizer shape : ', normalizer.shape)   
    print('    gauss norm       : ', gauss_heatmap_norm.shape   ,' Keras tensor ', KB.is_keras_tensor(gauss_heatmap_norm) )

    ##--------------------------------------------------------------------------------------------
    ## (6) Transpose heatmap to shape required for FCN
    ##--------------------------------------------------------------------------------------------
    # gauss_heatmap  = tf.transpose(gauss_heatmap,[0,2,3,1], name = names[0])
    # print('    gauss_heatmap : ', gauss_heatmap.shape,' Keras tensor ', KB.is_keras_tensor(gauss_heatmap))
    
    gauss_heatmap_norm = tf.transpose(gauss_heatmap_norm,[0,2,3,1], name = names[0]+'_norm')
    print('    gauss_heatmap_norm : ', gauss_heatmap_norm.shape,' Keras tensor ', KB.is_keras_tensor(gauss_heatmap_norm) )
    
    ##--------------------------------------------------------------------------------------------
    ## (7) Generate scores using prob_grid and pt2_dense - NEW METHOD
    ##     added 09-21-2018
    ##--------------------------------------------------------------------------------------------
    scores_from_sum2 = tf.map_fn(build_hm_score_original, 
                                 [prob_grid, pt2_dense_scaled, pt2_dense[:,7]], 
                                 dtype = tf.float32, swap_memory = True)
    scores_scattered = tf.scatter_nd(pt2_ind, scores_from_sum2, 
                                     [batch_size, num_classes, rois_per_image, 3], name = 'scores_scattered')
    gauss_scores = tf.concat([in_tensor, scores_scattered], axis = -1,name = names[0]+'_scores')
    print('    scores_scattered shape : ', scores_scattered.shape) 
    print('    gauss_scores           : ', gauss_scores.shape, ' Name:   ', gauss_scores.name)
    print('    gauss_scores  (FINAL)  : ', gauss_scores.shape, ' Keras tensor ', KB.is_keras_tensor(gauss_scores) )      
    print('    complete')
    return   gauss_heatmap_norm, gauss_scores  
    
    #---------------------------------------------------------------------------------------------
    #   Normalization is already perfored on the scores at a per_class leve, so we dont use this 
    #  code below anympre
    #
    #  This is a regular normalization that moves everything between [0, 1]. 
    #  This causes negative values to move to -inf, which is a problem in FCN scoring. 
    #  To address this a normalization between [-1 and +1] was introduced in FCN.
    #  Not sure how this will work with training tho.
    #----------------------------------------------------------------------------------------------
    #     normalizer   = tf.reduce_max(scores_scatt[...,-1], axis = -1, keepdims=True)
    #     print('norm',normalizer.shape)
    #     normalizer   = tf.where(normalizer < 1.0e-15,  tf.ones_like(normalizer), normalizer)
    #     norm_score2   = tf.expand_dims(scores_scatt[...,-1]/normalizer, axis = -1)
    #     print('norm_SCORE2',norm_score2.shape)
    #----------------------------------------------------------------------------------------------
    
    
    #----------------------------------------------------------------------------------------------
    #  Generate scores using GAUSS_SUM -- OLD METHOD
    #  removed 09-21-2018
    #----------------------------------------------------------------------------------------------
    #   Generate scores : 
    #   -----------------
    #  NOTE: Score is generated on NORMALIZED gaussian distributions (GAUSS_NORM)
    #        If want to do this on NON-NORMALIZED, we need to apply it on GAUSS_SUM
    #        Testing demonstated that the NORMALIZED score generated from using GAUSS_SUM 
    #        and GAUSS_NORM are the same. 
    #        For now we will use GAUSS_SUM score and GAUSS_NORM heatmap. The reason being that 
    #        the raw score generated in GAUSS_SUM is much smaller. 
    #        We may need to change this base on the training results from FCN 
    #---------------------------------------------------------------------------------------------
    #   duplicate GAUSS_NORM <num_roi> times to pass along with bboxes to map_fn function
    # 
    #   Here we have a choice to calculate scores using the GAUSS_SUM (unnormalized) or GAUSS_NORM (normalized)
    #   after looking at the scores and ratios for each option, I decided to go with the normalized 
    #   as the numbers are larger
    #
    #   Examples>
    #   Using GAUSS_SUM
    # [   3.660313    3.513489   54.475536   52.747402    1.   0.999997    4.998889 2450.  0.00204   0.444867]
    # [   7.135149    1.310972   50.020126   44.779854    1.   0.999991    4.981591 1892.  0.002633  0.574077]
    # [  13.401865    0.         62.258957   46.636948    1.   0.999971    4.957398 2303.  0.002153  0.469335]
    # [   0.          0.         66.42349    56.123024    1.   0.999908    4.999996 3696.  0.001353  0.294958]
    # [   0.          0.         40.78952    60.404335    1.   0.999833    4.586552 2460.  0.001864  0.406513]    
    #                                                                                               
    #   Using GAUSS_NORM:                             class   r-cnn scr                             
    # [   3.660313    3.513489   54.475536   52.747402    1.   0.999997 1832.9218   2450.  0.748131  0.479411]
    # [   7.135149    1.310972   50.020126   44.779854    1.   0.999991 1659.3965   1892.  0.877059  0.56203 ]
    # [  13.401865    0.         62.258957   46.636948    1.   0.999971 1540.4974   2303.  0.668909  0.428645]
    # [   0.          0.         66.42349    56.123024    1.   0.999908 1925.3267   3696.  0.520922  0.333813]
    # [   0.          0.         40.78952    60.404335    1.   0.999833 1531.321    2460.  0.622488  0.398898]
    # 
    #  to change the source, change the following line gauss_heatmap_norm <--> gauss_heatmap
    #--------------------------------------------------------------------------------------------------------- 
    # flatten guassian scattered and input_tensor, and pass on to build_bbox_score routine 
    # in_shape = tf.shape(in_tensor)
    # print('    shape of in_tensor is : ', KB.int_shape(in_tensor))
    # in_tensor_flattened  = tf.reshape(in_tensor, [-1, in_shape[-1]])  <-- not a good reshape style!! 
    # replaced with following line:
    # in_tensor_flattened  = tf.reshape(in_tensor, [-1, in_tensor.shape[-1]])
    #
    #  bboxes = tf.to_int32(tf.round(in_tensor_flattened[...,0:4]))
    #
    # print('    in_tensor             : ', in_tensor.shape)
    # print('    in_tensor_flattened   : ', in_tensor_flattened.shape)
    # print('    Rois per class        : ', rois_per_image)
    #
    #     print('\n    Scores from gauss_heatmap ----------------------------------------------')
    #     temp = tf.expand_dims(gauss_heatmap, axis =2)
    #     print('    temp expanded          : ', temp.shape)
    #     temp = tf.tile(temp, [1,1, rois_per_image ,1,1])
    #     print('    temp tiled shape       : ', temp.shape)
    # 
    #     temp = KB.reshape(temp, (-1, temp.shape[-2], temp.shape[-1]))
    #     
    #     print('    temp flattened         : ', temp.shape)
    #     print('    in_tensor_flattened    : ', in_tensor_flattened.shape)
    # 
    #     scores_from_sum = tf.map_fn(build_hm_score, [temp, in_tensor_flattened], dtype=tf.float32)
    #     scores_shape    = [in_tensor.shape[0], in_tensor.shape[1], in_tensor.shape[2], -1]
    #     scores_from_sum = tf.reshape(scores_from_sum, scores_shape)    
    #     print('    reshaped scores        : ', scores_from_sum.shape)
    #--------------------------------------------------------------------------------------------
    #  tf.reduce_max(scores_from_sum[...,-1], axis = -1, keepdims=True) result is [num_imgs, num_class, 1]
    #
    #  This is a regular normalization that moves everything between [0, 1]. 
    #  This causes negative values to move to -inf, which is a problem in FCN scoring. 
    #  To address this a normalization between [-1 and +1] was introduced in FCN.
    #  Not sure how this will work with training tho.
    #--------------------------------------------------------------------------------------------
    #     normalizer   = tf.reduce_max(scores_from_sum[...,-1], axis = -1, keepdims=True)
    #     normalizer   = tf.where(normalizer < 1.0e-15,  tf.ones_like(normalizer), normalizer)
    #     norm_score   = tf.expand_dims(scores_from_sum[...,-1]/normalizer, axis = -1)
    #--------------------------------------------------------------------------------------------
    # Append `in_tensor` and `scores_from_sum` to form `bbox_scores`
    #--------------------------------------------------------------------------------------------
    #     gauss_scores = tf.concat([in_tensor, scores_from_sum, norm_score], axis = -1,name = names[0]+'_scores')
    #     print('    scores_from_sum final  : ', scores_from_sum.shape)    
    #     print('    norm_score             : ', norm_score.shape)
    #     print('    gauss_scores           : ', gauss_scores.shape,  '   name:   ', gauss_scores.name)
    #--------------------------------------------------------------------------------------------------------------------
    


    
##-----------------------------------------------------------------------------------------------------------
## Build Mask and Score 
##----------------------------------------------------------------------------------------------------------- 
def build_hm_score_original(input_list):
    '''
    Inputs:
    -----------
        heatmap_tensor :    [ image height, image width ]
        input_row      :    [y1, x1, y2, x2] in absolute (non-normalized) scale

    Returns
    -----------
        gaussian_sum :      sum of gaussian heatmap vlaues over the area covered by the bounding box
        bbox_area    :      bounding box area (in pixels)
        weighted_sum :      gaussian_sum * bbox_score
    '''
    heatmap_tensor, input_bbox, input_norm_score = input_list
    
    with tf.variable_scope('mask_routine'):
        y_extent     = tf.range(input_bbox[0], input_bbox[2])
        x_extent     = tf.range(input_bbox[1], input_bbox[3])
        Y,X          = tf.meshgrid(y_extent, x_extent)
        bbox_mask    = tf.stack([Y,X],axis=2)        
        mask_indices = tf.reshape(bbox_mask,[-1,2])
        mask_indices = tf.to_int32(mask_indices)
        mask_size    = tf.shape(mask_indices)[0]
        mask_updates = tf.ones([mask_size], dtype = tf.float32)    
        mask         = tf.scatter_nd(mask_indices, mask_updates, tf.shape(heatmap_tensor))
        # mask_sum    =  tf.reduce_sum(mask)
        mask_applied = tf.multiply(heatmap_tensor, mask, name = 'mask_applied')
        bbox_area    = tf.to_float((input_bbox[2]-input_bbox[0]) * (input_bbox[3]-input_bbox[1]))
        gaussian_sum = tf.reduce_sum(mask_applied)

        # Multiply gaussian_sum by score to obtain weighted sum    
        # weighted_sum = gaussian_sum * input_row[5]
        # Replaced lines above with following lines 21-09-2018
        # Multiply gaussian_sum by normalized score to obtain weighted_norm_sum 
        weighted_norm_sum = gaussian_sum * input_norm_score    # input_list[7]

    return tf.stack([gaussian_sum, bbox_area, weighted_norm_sum], axis = -1)
"""
              