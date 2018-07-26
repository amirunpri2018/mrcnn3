import os
import sys
import glob
import random
import math
import datetime
import itertools
import json
import re
import logging
# from collections import OrderedDict
import numpy as np
from scipy.stats import  multivariate_normal
# import scipy.misc
import tensorflow as tf
# import keras
import keras.backend as KB
import keras.layers as KL
import keras.engine as KE
sys.path.append('..')
import mrcnn.utils as utils
import tensorflow.contrib.util as tfc
import pprint

     
def build_predictions(mrcnn_class, mrcnn_bbox, norm_output_rois, config):
    # // pass model to TensorBuilder
    num_images  = config.BATCH_SIZE
    num_classes = config.NUM_CLASSES
    num_rois    = config.TRAIN_ROIS_PER_IMAGE
    print('num rois :',num_rois)
    h, w        = config.IMAGE_SHAPE[:2]
    num_cols    = 8 

    # mdl_outputs[outroi_idx] returns the normalized coordinates, we multiply by h,w to get true coordinates
    pred_tensor = np.zeros((num_images, num_classes, num_rois, num_cols ), dtype=np.float32)      # img_in_batch, 4, 32, 8
    pred_cls_cnt= np.zeros((num_images, num_classes), dtype=np.int16)
    output_rois = norm_output_rois * np.array([h,w,h,w])   
    pred_new    = np.empty((num_rois, num_cols))
    # print('mrcnn_class shape : ', mrcnn_class.shape, '\t mrcnn_bbox.shape : ', mrcnn_bbox.shape )
    # print('output_rois.shape : ', output_rois.shape, '\t pred_tensor shape: ', pred_tensor.shape  )

    #---------------------------------------------------------------------------
    # use the argmaxof each row to determine the dominating (predicted) class
    #---------------------------------------------------------------------------

    
    for img in range(num_images):
        img_roi_scores  = mrcnn_class[img]              # 2x32x4 -> 32x4
        img_roi_boxes   = output_rois[img] 
        # img_roi_boxes   = output_rois[~np.all(output_rois[img] != 0, axis=0)]
        # print(' before \n', output_rois[img])
        # print(' after  \n', img_roi_boxes)
        _pred_class     = np.argmax(img_roi_scores,axis=1)   # (32,)

        print('----------------------------------------------------------')
        print(' image: ' , img)
        print('----------------------------------------------------------')       
        # print('mrcnn_class[img] ',img_roi_scores.shape)
        # print(img_roi_scores)
        print('output_rois[img] ',img_roi_boxes.shape)
        print(img_roi_boxes)
        # print('img: ',img , 'pred_cls: ', _pred_class)
        
        for cls in range(num_classes) :
            cls_idxs = np.where( _pred_class == cls )
            cls_cnt  = cls_idxs[0].shape[0]
            pred_new.fill(0)
            print('----------------------------------------------------------')
            print(' img/cls is: ' , img,'/',cls, 'cls_idxs: ' , cls_idxs[0])
            print(' cls_idxs[0].shape: ', cls_idxs[0].shape, ' cls_cnt',cls_cnt)
            print('----------------------------------------------------------')
            score = np.max(img_roi_scores[cls_idxs],axis = -1)
            pred_new[:cls_cnt,0]  = cls_idxs[0]
            pred_new[:cls_cnt,1]  = score
            pred_new[:cls_cnt,2:6]= img_roi_boxes[cls_idxs]
            pred_new[:cls_cnt,6]  = cls
            pred_new[:cls_cnt,7]  = range(cls_cnt)
            

            # print(' mrcnn_class: ', img_roi_scores.shape)
            # print(  img_roi_scores[cls_idxs])
            # print(' score.shape  : ', score.shape)
            # print(  score)
            # print(' img_roi_boxes.shape',  img_roi_boxes[cls_idxs].shape)
            
            ## sort pred_new array in descending prediction order 
            order                  = pred_new[:cls_cnt,1].argsort()
            pred_new[:cls_cnt,:7]  = pred_new[order[::-1] ,:7]      #[img, cls,::-1]           
            print('pred_new[img,cls] after sort:')
            print(pred_new)

            
            ##  drop (0,0,0,0) bounding boxes from pred_new array just constructed 
            # cls_boxes  = pred_new[:cls_cnt,2:6]
            # vld_indices   = ~np.all( pred_new[:,2:6] == 0, axis=1)
            # non_zero_rois = np.count_nonzero(vld_indices)
            # print('vld_indices  \n' , vld_indices.shape, 'non zero bounding boxes: ', non_zero_rois)
            # print(vld_indices)

            # pred_tensor[img,cls]  = np.pad(pred_new[vld_indices], ((0, num_rois - non_zero_rois),(0,0)),'constant', constant_values = 0)
            # print('pred_new after suppression of zero bboxes  \n' , pred_tensor[img, cls])
            # pred_cls_cnt[img, cls] = non_zero_rois

            pred_tensor[img,cls]   = pred_new
            pred_cls_cnt[img, cls] = cls_cnt
            
            print(' pred_cls_cnt is ' , pred_cls_cnt)
    return  [pred_tensor, pred_cls_cnt]


    
def build_predictions_tf(mrcnn_class, mrcnn_bbox, norm_output_rois, config):
    # // pass model to TensorBuilder
    batch_size      = config.BATCH_SIZE
    num_classes     = config.NUM_CLASSES
    num_rois        = config.TRAIN_ROIS_PER_IMAGE
    h, w            = config.IMAGE_SHAPE[:2]
    num_cols        = 7 
    print('>>> build_predictions_tf' )
    sess = tf.InteractiveSession()
    
    output_rois = norm_output_rois * np.array([h,w,h,w])   

    # print('>>> build_predictions_tf')
    # print('    mrcnn_class shape : ', mrcnn_class.shape)
    # print('    mrcnn_bbox.shape  : ', mrcnn_bbox.shape )
    # print('    output_rois.shape : ', output_rois.shape)
    # print('    pred_tensor       : ', pred_tensor.shape)
    # print('    pred_cls_cnt      : ', pred_cls_cnt.shape)
    #---------------------------------------------------------------------------
    # use the argmaxof each row to determine the dominating (predicted) class
    #---------------------------------------------------------------------------
    # np.set_printoptions(linewidth=100, precision=4)
    bbox_selected    = tf.zeros_like(norm_output_rois)
    pred_classes     = tf.to_int32(tf.argmax( mrcnn_class,axis=-1))
    pred_classes_exp = tf.to_float(tf.expand_dims(pred_classes ,axis=-1))
    pred_scores      = tf.reduce_max(mrcnn_class ,axis=-1, keepdims=True)   # (32,)
    # print('    pred_classes with highest scores:', pred_classes.get_shape() )
    # pred_scores_exp = tf.to_float(tf.expand_dims(pred_scores, axis=-1))
    # print('    pred_ scores:', pred_scores.get_shape())
    

    batch_grid, roi_grid = tf.meshgrid( tf.range(batch_size, dtype=tf.int32), tf.range(num_rois, dtype=tf.int32), indexing = 'ij' )
    bbox_idx             = tf.to_float(tf.expand_dims(roi_grid , axis = -1))    
    scatter_ind          = tf.stack([batch_grid , pred_classes, roi_grid],axis = -1)
    # print('-- stack results ----')
    # print('scatter_ind', type(scatter_ind), 'shape',tf.shape(scatter_ind).eval())
    # print(scatter_ind.eval())

    #-----------------------------------------------------------------------------------
    # This part is used if we want to gather bbox coordinates from mrcnn_bbox 
    #  Currently we are gathering bbox coordinates form output_roi so we dont need this
    #-----------------------------------------------------------------------------------
    # gather_boxes    = tf.stack([batch_grid, roi_grid, pred_classes, ], axis = -1)
    # print('-- gather_boxes  ----')
    # print('gather_boxes inds', type(gather_boxes), 'shape',tf.shape(gather_boxes).eval())
    # print(gather_boxes.eval())
    # bbox_selected   = tf.gather_nd(mrcnn_bbox, gather_boxes)
    # print('    bbox_selected shape : ', bbox_selected.get_shape())
    # print(bbox_selected[0].eval())    
    

    pred_array  = tf.concat([bbox_idx, pred_scores , output_rois, pred_classes_exp], axis=2)
    print(' -- pred_tensor tf ------------------------------') 
    print('    resulting tensor : a_boxes_3d',  type(pred_array), pred_array.shape)
    
    # pred_array = pred_array[~np.all(pred_array[:,:,2:6] == 0, axis=1)]
    
    class_ids = tf.to_int32(pred_array[:,:,6])
    # print('    class shape: ', class_ids.get_shape())
    # print(class_ids.eval())

    # print('    roi_grid ', type(roi_grid), 'shape', roi_grid.get_shape())
    # print(roi_grid.eval())
    # print('    batch_grid     ', type(batch_grid), 'shape',(batch_grid.get_shape()))
    # print(batch_grid.eval())

    pred_scatt = tf.scatter_nd(scatter_ind, pred_array, [batch_size, num_classes, num_rois,7])
    print('    pred_scatter shape is ', pred_scatt.get_shape(), pred_scatt)
    
    ## sort in each class dimension based on prediction score 

    _, sort_inds = tf.nn.top_k(pred_scatt[:,:,:,1], k=pred_scatt.shape[2])
    print('    sort inds shape : ', sort_inds.get_shape())

    # build gathering indexes to use in sorting 
    class_grid, batch_grid, roi_grid = tf.meshgrid(tf.range(num_classes),tf.range(batch_size), tf.range(num_rois))
    roi_grid_exp = tf.expand_dims(roi_grid, axis = -1)
    gather_inds  = tf.stack([batch_grid , class_grid, sort_inds],axis = -1)
    pred_tensor  = tf.gather_nd(pred_scatt, gather_inds)
    pred_tensor  = tf.concat([pred_tensor, tf.to_float(roi_grid_exp)], axis = -1)
    
    print('    class_grid  ', type(class_grid) , 'shape', class_grid.get_shape())
    print('    batch_grid  ', type(batch_grid) , 'shape', batch_grid.get_shape())
    print('    roi_grid    ', type(roi_grid)   , 'shape', roi_grid.get_shape())
    print('    gather_inds ', type(gather_inds), 'shape', gather_inds.get_shape())
    print('    -- pred_tensor results (A-boxes sorted by score ----')
    print('    pred_tensor ', pred_tensor.get_shape())

    pred_cls_cnt = tf.count_nonzero(pred_tensor[:,:,:,0],axis = -1)
    print('    pred_cls_cnt shape : ',pred_cls_cnt.get_shape())
    print('    complete')

    return  [pred_tensor, pred_cls_cnt] 
    
    
    
def build_ground_truth(gt_class_ids, norm_gt_bboxes, config):
    # // pass model to TensorBuilder
    num_images      = config.BATCH_SIZE
    num_classes     = config.NUM_CLASSES
    num_detections  = config.DETECTION_MAX_INSTANCES
    h, w            = config.IMAGE_SHAPE[:2]
    num_cols        = 8 

    gt_tensor   = np.zeros((num_images, num_classes, num_detections, num_cols ), dtype=np.float32)      # img_in_batch, 4, 32, 8  
    gt_cls_cnt  = np.zeros((num_images, num_classes), dtype=np.int16)
    gt_bboxes   = norm_gt_bboxes   * np.array([h,w,h,w])   
    gt_new      = np.empty((num_detections, num_cols))
        # gt_masks   = sample_x[gtmsk_idx][0,:,:,nz_idx]
    # print('gt_class_ids shape : ', gt_class_ids.shape, '\t norm_gt_bboxes.shape : ', norm_gt_bboxes.shape )
    # print('\n',gt_class_ids)
    # print('\n',gt_bboxes)

    #---------------------------------------------------------------------------           
    #  generate ground truth tensors 
    # note - we ignore the background (class 0) in the ground truth
    #---------------------------------------------------------------------------
    for img in range(num_images):
    
        for cls in range(1, num_classes) :
     
            cls_idxs = np.where( gt_class_ids[img, :] == cls)
            cls_cnt  = cls_idxs[0].shape[0] 
            # print('img is: ' , img , 'class: ', cls,  'cls_idxs: ' , cls_idxs)
            gt_new.fill(0)
            gt_new[:cls_cnt,0]  = range(cls_cnt)
            gt_new[:cls_cnt,1]  = 1.0
            gt_new[:cls_cnt,2:6]= gt_bboxes[img, cls_idxs,:]
            gt_new[:cls_cnt,6]  = cls
            gt_new[:cls_cnt,7]  = cls_idxs[0]
            
            # for j , c_idx in enumerate(cls_idxs):        
                # gt_tensor[img, cls, j,  0]  = j
                # gt_tensor[img, cls, j,  1]  = 1.0                                 # probability
                # gt_tensor[img, cls, j, 2:6] = gt_bboxes[img,c_idx,:]                         # roi coordinates
                # gt_tensor[img, cls, j,  6]  = cls                                 # class_id
                # gt_tensor[img, cls, j,  7]  = c_idx                               # index from mrcnn_class array (temp for verification)
            # print(gt_tensor[img,cls])
            gt_tensor[img,cls]   = gt_new
            gt_cls_cnt[img, cls] = cls_cnt
            # print('gt_tensor is')
    return  [gt_tensor, gt_cls_cnt]

    
    
def build_ground_truth_tf(gt_class_ids, norm_gt_bboxes, config):
    # // pass model to TensorBuilder
    batch_size      = config.BATCH_SIZE
    num_classes     = config.NUM_CLASSES
    num_detections  = config.DETECTION_MAX_INSTANCES
    h, w            = config.IMAGE_SHAPE[:2]
    num_cols        = 7 
    print('>>> build_ground_truth_tf' )
    print('    gt_class_ids shape : ', gt_class_ids.shape, '    notm_gt_bbox.shape  : ', norm_gt_bboxes.shape )

    # sess = tf.InteractiveSession()
    gt_bboxes       = norm_gt_bboxes * np.array([h,w,h,w])   

    #---------------------------------------------------------------------------
    # use the argmaxof each row to determine the dominating (predicted) class
    #---------------------------------------------------------------------------
        
    # gt_classes     = gt_class_ids    # batch_size x max gt detections
    gt_classes_exp = tf.to_float(tf.expand_dims(gt_class_ids ,axis=-1))
    print('    gt_classes_exp shape ', gt_classes_exp.get_shape() )

    ones = tf.ones_like(gt_class_ids)
    zeros= tf.zeros_like(gt_class_ids)
    mask = tf.greater(gt_class_ids , 0)

    gt_scores  =  tf.where(mask, ones, zeros)
    # pred_scores      = tf.reduce_max(mrcnn_class ,axis=-1, keep_dims=True)   # (32,)
    gt_scores_exp = tf.to_float(tf.expand_dims(gt_scores, axis=-1))
    print('    pred_ scores shape ', gt_scores.get_shape())
    

    batch_grid, bbox_grid = tf.meshgrid( tf.range(batch_size    , dtype=tf.int32), 
                                         tf.range(num_detections, dtype=tf.int32), indexing = 'ij' )
    
    print('    bbox_grid  shape  ', bbox_grid.get_shape())
    # print(bbox_grid.eval())
    print('    batch_grid  shape ', batch_grid.get_shape())
    # print(batch_grid.eval())
    
    bbox_idx_zeros  = tf.zeros_like(bbox_grid)
    bbox_idx        = tf.where(mask, bbox_grid , bbox_idx_zeros)
    bbox_idx        = tf.to_float(tf.expand_dims(bbox_idx, axis = -1))    
    print('    bbox_idx shape   ', bbox_idx.get_shape())
    # print(bbox_idx.eval())
    
    gt_array        = tf.concat([bbox_idx, gt_scores_exp , gt_bboxes, gt_classes_exp], axis=2)
    print('    gt_array shape   ', gt_array.get_shape())
    
    # dont need this as gt_class_ids is already int
    #class_ids = tf.to_int32(gt_array[:,:,6])
    
    print('    class shape      ', gt_class_ids.get_shape())
    # print(class_ids.eval())
    print('    roi_grid   shape ', bbox_grid.get_shape())
    # print(roi_grid.eval())
    print('    batch_grid shape ', batch_grid.get_shape())
    # print(batch_grid.eval())

    scatter_ind = tf.stack([batch_grid , gt_class_ids, bbox_grid],axis = -1)
    # print('-- stack results ----')
    print('    scatter_ind shape ', scatter_ind.get_shape())
    # print(scatter_ind.eval())
    
    gt_scatter = tf.scatter_nd(scatter_ind, gt_array, [batch_size, num_classes, num_detections,7])
    print('    gt_scatter shape ', gt_scatter.get_shape())
    
    ## sort in each class dimension based on index (column 0)

    _ , sort_inds = tf.nn.top_k(gt_scatter[:,:,:,0], k=gt_scatter.shape[2])
    print('    sort inds shape : ', sort_inds.get_shape())
    # print(sort_inds.eval())

    # build gathering indexes to use in sorting 
    class_grid, batch_grid, bbox_grid = tf.meshgrid(tf.range(num_classes),tf.range(batch_size), tf.range(num_detections))

    print('    class_grid  shape ', class_grid.get_shape())
    # print(class_grid.eval())
    print('    batch_grid  shape ', batch_grid.get_shape())
    # print(class_grid.eval())
    print('    bbox_grid   shape ', bbox_grid.get_shape())
    # print(bbox_grid.eval())

    gather_inds = tf.stack([batch_grid , class_grid, sort_inds],axis = -1)
    print('    gather_inds shape ', gather_inds.get_shape())

    gt_tensor = tf.gather_nd(gt_scatter, gather_inds)
    print('    pred_tensor shape ', gt_tensor.get_shape())

    gt_cls_cnt = tf.count_nonzero(gt_tensor[:,:,:,0],axis = -1)
    print('    gt_cls_cnt shape : ', gt_cls_cnt.get_shape())
    print('    complete')

    return  [gt_tensor, gt_cls_cnt] 
    
    

    
def get_stacked(in_tensor, in_cls_cnt, config):
    # print('gt_stacked: input _cs_cnt type/shape' , type(in_cls_cnt), in_cls_cnt.shape)
    _stacked = []
    for img in range(config.BATCH_SIZE):
        _substack = np.empty((0,8),dtype=np.float32)
        for cls in range(config.NUM_CLASSES):
            _substack = np.vstack((_substack, in_tensor[img, cls, 0 : in_cls_cnt[img, cls]] ))   
        _stacked.append(np.asarray(_substack))                

    return _stacked
    

    
    
def build_gaussian_np(in_tensor, in_cls_cnt, config):
    from scipy.stats import  multivariate_normal
    img_h, img_w = config.IMAGE_SHAPE[:2]
    num_images   = config.BATCH_SIZE
    num_classes  = config.NUM_CLASSES  
    means = []
    covar = [] 
    
    # rois per image is determined by size of input tensor 
    #   detection mode:   config.TRAIN_ROIS_PER_IMAGE 
    #   ground_truth  :   config.DETECTION_MAX_INSTANCES    
    rois_per_image   = in_tensor.shape[2]     
    strt_cls = 0 if rois_per_image == 32 else 1

    print('   input_tensor shape is ', in_tensor.shape)
    print('   num of bboxes per class is : ', rois_per_image)

    
    # if rois_per_image == 100:
        # print('\n',in_tensor[0,0])
        # print('\n',in_tensor[0,1])
        # print('\n',in_tensor[0,2])
        # print('\n',in_tensor[0,3])

    # Build mesh-grid to hold pixel coordinates ----------------------------------
    X = np.arange(0, img_w, 1)
    Y = np.arange(0, img_h, 1)
    X, Y = np.meshgrid(X, Y)
    pos  = np.empty((rois_per_image,) + X.shape + (2,))   # concatinate shape of x to make ( x.rows, x.cols, 2)
    pos[:,:,:,0] = X;
    pos[:,:,:,1] = Y;

    # Build the covariance matrix ------------------------------------------------
    cov = np.zeros((rois_per_image ,2))* np.array([12,19])
    # k_sess = KB.get_session()    
    
    in_stacked = get_stacked(in_tensor, in_cls_cnt, config)    
    # print(' stacked length is ', len(in_stacked), ' shape is ', in_stacked[0].shape)
    
    Zout  = np.zeros((num_images, num_classes, img_w, img_h), dtype=np.float32)
    cls_mask = np.empty((img_w, img_h), dtype=np.int8)
    print(' COVARIANCE SHAPE:',cov.shape)
    # print(' Pred Tensor  is :', _tensor)
    # print('PRT SHAPES:', pred_stacked[0].shape, pred_stacked[1].shape)   
    
    for img in range(num_images):
        print('====> Img: ', img)
        psx     = in_stacked[img]   #.eval(session = k_sess)  #     .eval(session=k_sess)
        # remove bboxes with zeros  
        # print('  input tensor shape _: ',psx.shape)            
        # print(psx)    
        ## remove zero bouding boxes
        ps = psx[~np.all(psx[:,2:6] == 0, axis=1)]
        # print('  input tensor after zeros removals shape _: ',ps.shape)            
        # print(ps)            

        width  = ps[:,5] - ps[:,3]
        height = ps[:,4] - ps[:,2]
        cx     = ps[:,3] + ( width  / 2.0)
        cy     = ps[:,2] + ( height / 2.0)
        mns    = np.stack((cx,cy),axis = -1)
        cov    = np.stack((width * 0.5 , height * 0.5), axis = -1)

        mns_pad  = np.pad(mns, ((0, rois_per_image - mns.shape[0]), (0,0)), 'constant', constant_values = 0)
        cov_pad  = np.pad(cov, ((0, rois_per_image - cov.shape[0]), (0,0)), 'constant', constant_values = 0)
        means.append(mns_pad)
        covar.append(cov_pad)
        print(' ===> mns shape ', mns_pad.shape, 'coar.shape ',cov_pad.shape)
        # weight = np.ones((ps.shape[0]))
        # print('cov ', cov)
        #--------------------------------------------------------------------------------
        # kill boxes with height/width of zero which cause singular sigma cov matrices
        # zero_boxes = np.argwhere(width+height == 0)
        # print('zero boxes ' , zero_boxes) 
        # cov[zero_boxes] = [1,1]
        # print('cov ', cov)
        # print(ps.shape, type(ps),width.shape, height.shape, cx.shape, cy.shape)
        
        # print('  img : ', img, ' means.shape:', means.shape, 'cov.shape ', cov.shape)
        rv  = list( map(multivariate_normal, mns, cov))
        # print('  size of rv is ', len(rv))
        pdf = list( map(lambda x,y: x.pdf(y) , rv, pos))
        # print('  size of pdf is ', len(pdf))
        # pdf_arr = np.asarray(pdf)       # PDF_ARR.SHAPE = # detection rois per image X  image_width X image_height
        pdf_arr = np.dstack(pdf)       # PDF_ARR.SHAPE = # detection rois per image X  image_width X image_height
        # print('  pdf_arr.shape : ' ,pdf_arr.shape)

        for cls in range(strt_cls, num_classes):
            _class_idxs = np.argwhere(ps[:,6] == cls).squeeze(axis=-1)
            # print('   *** img: ', img,' cls:',cls,' ',np.squeeze(_class_idxs))
            if _class_idxs.shape[0] == 0:
                continue
            ps_cls = ps[_class_idxs,:]

            ## build class specific mask based on bounding boxes
            # print('ps_cls : \n')
            # print(ps_cls)
            cls_boxes  = ps_cls[:,2:6]
            vld_boxes  = cls_boxes[~np.all(cls_boxes == 0, axis=1)]
            vld_boxes  = np.round(vld_boxes).astype(np.int)
            cls_mask.fill(0)
            
            # print('vld_boxes  \n' , vld_boxes)
            for i in vld_boxes:
                # _tmp[ slice(i[0],i[2]) , slice(i[1],i[3]) ]
                # print('(slice(',i[0],',',i[2],'), slice(',i[1],',',i[3],'))')
                # cls_mask[ slice(i[0],i[2]) , slice(i[1],i[3]) ] += 1
                cls_mask[ slice(i[0],i[2]) , slice(i[1],i[3]) ] = 1
                
            # slice_list = [(slice(i[0],i[2]), slice(i[1],i[3])) for i in vld_boxes]
            # print('Slice list \n')
            # pp = pprint.PrettyPrinter(indent=2, width=100)
            # pp.pprint(slice_list)
            # cls_mask = np.fill(0)
            # for i in slice_list:
                # cls_mask += 

            ##--------------------------------------------------
            norm   = np.sum(ps_cls[:,1])
            weight = ps_cls[:,1]/norm
            nones  = np.ones_like(weight)
            # print('    ps_cls shpe:',ps_cls.shape)            
            # print('    ps_cls :  \n', ps_cls)    
            
            # print('    Norm   :    ', norm)
            # print('    Weight shape', weight.shape, '  ', weight)
            # print('    pdf_arr[class,..] shape: ', pdf_arr[...,_class_idxs].shape)

            pdf_arr_abs = pdf_arr[...,_class_idxs]
            pdf_arr_wtd = pdf_arr[...,_class_idxs] * weight

            # print('    pdf_arr_wtd shape:       ', pdf_arr_wtd.shape)
            pdf_sum_wtd = np.sum(pdf_arr_wtd,axis=-1)
            pdf_sum_abs = np.sum(pdf_arr_abs,axis=-1)

            # print('    Weighted max/min ',np.max(np.max(pdf_arr_wtd),0),np.min(np.min(pdf_arr_wtd),0))
            # print('    Absolute max/min ',np.max(np.max(pdf_arr_abs),0),np.min(np.min(pdf_arr_abs),0))
            # print('    pdf_sum_wtd.shape ,: ' ,pdf_sum_wtd.shape , '   pdf_sum_abs.shape: ',pdf_sum_abs.shape)
            ###  print mask 
            # if rois_per_image == 100:
                # np.set_printoptions(threshold=99999, linewidth=2000)
                # print(np.array2string(cls_mask ,max_line_width=2000,separator=''))
            
            # Zout[img,cls] += np.sum(pdf_arr[_class_idxs],axis=0)[0]
            Zout[img,cls] = np.multiply(pdf_sum_abs, cls_mask) 

            
    print('Zout shape:',Zout.shape)
    # print(Zout)
    # if rois_per_image == 100:
            # print('cls_mask[0,0]\n',cls_mask[0,0])
            # print('cls_mask[0,1]\n',cls_mask[0,1])
            # print('cls_mask[0,2]\n',cls_mask[0,2])
            # print('cls_mask[0,3]\n',cls_mask[0,3])
            # print('cls_mask[1,0]\n',cls_mask[1,0])
            # print('cls_mask[1,1]\n',cls_mask[1,1])
            # print('cls_mask[1,2]\n',cls_mask[1,2])
            # print('cls_mask[1,3]\n',cls_mask[1,3])
                
    covar_arr = np.stack(covar, axis = 0)
    means_arr = np.stack(means, axis = 0)
    print('covar_arrc : ', covar_arr.shape, '  means_arr shae', means_arr.shape)

    return  [Zout, means_arr, covar_arr]
    
    

    
    
def build_gaussian_tf(in_tensor, pred_cls_cnt, config):
        
    # rois_per_image  = 32
    
    num_detections  = config.DETECTION_MAX_INSTANCES
    h, w            = config.IMAGE_SHAPE[:2]
    num_cols        = 8
    img_h, img_w    = config.IMAGE_SHAPE[:2]
    batch_size      = config.BATCH_SIZE
    num_classes     = config.NUM_CLASSES  
    print('>>> build_gaussian_tf ')
    ## rois per image is determined by size of input tensor 
    ##   detection mode:   config.TRAIN_ROIS_PER_IMAGE 
    ##   ground_truth  :   config.DETECTION_MAX_INSTANCES
    in_tensor = in_tensor[:,:,:,1:]
    print(' in_tensor shapeis ', in_tensor.get_shape())
    
    tst1 = tf.shape(in_tensor)[2]
    print('type tsts1 : ',tst1 )
    tst2 = in_tensor.get_shape()[2]
    print('type tsts2 : ',tst2 )
    rois_per_image   = tf.to_int32(in_tensor.shape[2])
    strt_cls = 0 if rois_per_image == 32 else 1
    print('   num of bboxes per class is : ', rois_per_image)


    print('   pred_tensor shape is : ', in_tensor.shape)

    pt2_reshape = tf.reshape( in_tensor , [batch_size, num_classes * rois_per_image ,6])
    print('   pt2_reshape shape is : ', pt2_reshape.get_shape())
     
    # print(pt2_reshape[0].eval())
    # print(pt2_reshape[1].eval())
    # print(pt2_reshape[2].eval())

    pt2_sum = tf.reduce_sum(tf.abs(in_tensor[:,:,:,1:5]), axis=-1)
    print('   pt2_sum shape ',pt2_sum.shape)
    # print(pt2_sum[0].eval())

    pt2_mask = tf.greater(pt2_sum , 0)
    
    # print(' pt2_mask shape ', pt2_mask.get_shape())
    # print(pt2_mask.eval())

    pt2_ind  = tf.where(pt2_mask)
    print('   pt2_ind shape  ', pt2_ind.get_shape())
    # print(pt2_ind.eval())
    # pt2_ind_float  =  tf.to_float(pt2_ind[:,0:1])

    dense1 = tf.gather_nd( in_tensor, pt2_ind)
    print('   dense1 shape ',dense1.get_shape())
    # print(dense1.eval())

    dense1 = tf.concat([tf.to_float(pt2_ind[:,0:1]), dense1],axis=1)
    # print(' dense1 shape ',dense1.get_shape())
    # print(dense1.eval())

    # print(dense[1].eval())
    # print(dense[2].eval())
    # print(dense[3].eval())
    stacked_list = tf.dynamic_partition(dense1, tf.to_int32(pt2_ind[:,0]),num_partitions = batch_size )
    # print(len(dyn_part))      

    print('   -- Build Stacked output from dynamically partitioned lists --------------')  

    stacked_output=[]
    for img, item  in enumerate(stacked_list) :
        # rois_in_image, cols  = tf.shape(stacked_list[img]).eval()
        print('\n   ===> list item #', img)       
        # print('   img ', img, ' stacked_list[img] ', tf.shape(item).eval() ) 
        # print('   img ', img, ' stacked_list[img] ', tf.shape(item).eval()[0] ) 
        rois_in_image  = tf.shape(item)[0]
        print('   stacked_list[img] shape: ',rois_in_image)
        #     print(stacked_list[img].eval())            
        pad_item =  tf.pad(item,[[0, rois_per_image - rois_in_image ],[0,0]])
        stacked_output.append(pad_item)
        print('   tensor_list item pos padding :', tf.shape(pad_item))
        #     print(stacked_list[img].eval())

    print()    
    stacked_tensor = tf.stack(stacked_output)
    print('stacked output tensor shape : ', tf.shape(stacked_tensor), stacked_tensor.shape, stacked_tensor.get_shape())
    print('   -- Stacked output contents --------------')    
    for img, item  in enumerate(stacked_output) :
        print('\n   ===> list item #', img)       
        # print('   img ', img, ' stacked_list[img] ', tf.shape(item).eval() ) 
        # print('   img ', img, ' stacked_list[img] ', tf.shape(item).eval()[0] ) 
    stacked_tensor = tf.stack(stacked_output)
    print('   stacked tensor : ', tf.shape(stacked_tensor), stacked_tensor.shape, stacked_tensor.get_shape())


    ## Build mesh-grid to hold pixel coordinates ----------------------------------
    X = tf.range(img_w, dtype=tf.int32)
    Y = tf.range(img_h, dtype=tf.int32)
    X, Y = tf.meshgrid(X, Y)
    print( X.get_shape(), Y.get_shape())
    # print( ' X : \n',X.eval())
    # print( ' Y : \n',Y.eval())

    # ## hear we repeat X and Y  batch_size x rois_per_image times
    ones = tf.ones([batch_size, rois_per_image,1, 1], dtype = tf.int32)
    print('   ones: ',ones.shape)                
    # # ones = tf.expand_dims(ones,-1)
    # print(' ones with exp dims ',ones.shape)

    rep_X = ones * X
    rep_Y = ones * Y 
    # print(' ones_exp * X', ones.shape, '*', X.shape, '= ',rep_X.shape)
    # print(' ones_exp * Y', ones.shape, '*', Y.shape, '= ',rep_Y.shape)

    # # stack the X and Y grids 
    bef_pos = tf.to_float(tf.stack([rep_X,rep_Y], axis = -1))
    # print(' before transpse ', bef_pos.get_shape())
    pos_grid_1 = tf.transpose(bef_pos,[2,3,0,1,4])
    print('   after transpose ', pos_grid_1.get_shape())    

    
    ps = stacked_tensor
    # for img, ps_init in enumerate(stacked_tensor):
    # print('\n===> list memeber #', img, ' shape: ', ps_init.get_shape(), '  ',tf.shape(ps_init).eval())   
    # rois_in_image, cols  = tf.shape(ps_init).eval()
    # print(' ps \t rows :', rois_in_image, '\t cols: ', cols)

    # ps = tf.pad(ps_init,[[0,32-rois_in_image],[0,0]])

    # print('ps.shape is ', tf.shape(ps_init).eval())
    # print(ps_init.eval())    
    print('ps.shape is ', ps.get_shape())
    # print(ps.eval())

    width  = ps[:,:,5] - ps[:,:,3]
    height = ps[:,:,4] - ps[:,:,2]
    cx     = ps[:,:,3] + ( width  / 2.0)
    cy     = ps[:,:,2] + ( height / 2.0)
    means  = tf.stack((cx,cy),axis = -1)
    covar  = tf.stack((width * 0.5 , height * 0.5), axis = -1)
    # print(means.eval())
    # print(covar.eval())

    # print('width shape ',width.get_shape()) 
    mns = means
    cov = covar
    # print(mns.eval())
    tfd = tf.contrib.distributions
    mvn = tfd.MultivariateNormalDiag(
        loc  = mns,
        scale_diag = cov)

    print('   means shape ',means.get_shape(), '  ', means.get_shape())
    print('   covar shape ',covar.get_shape(), '  ', covar.get_shape())
    print('   from MVN :  \t mns shape      :', mns.shape, mns.get_shape(), ' \t cov shape : ', cov.shape, cov.get_shape())
    print('   from MVN :  \t mean shape     :', mvn.mean().shape, '\t stddev shape', mvn.stddev().shape )
    print('   from MVN :  \t mean shape     :', mvn.mean().get_shape(), '\t stddev shape', mvn.stddev().get_shape())
    print('   from MVN :  \t mvn.batch_shape:', mvn.batch_shape , '\t mvn.event_shape ',  mvn.event_shape)
    # print(  mvn.loc.eval())
    print('   Linear OP shape      ', mvn.scale.shape, ' Linear Op batch shape ',mvn.scale.batch_shape)
    print('   Linear op Range Dim  ', mvn.scale.range_dimension)
    print('   Linear op Domain Dim ', mvn.scale.domain_dimension) 
        
    inp = pos_grid_1
    print('   >> input to MVN.PROB: pos_grid (meshgrid) shape: ', inp.get_shape())
    # print(inp.eval())
    # one_layer = tf.to_float(pos[0])
    # print(one_layer[0].get_shape)

    prob_grid = mvn.prob(inp)
    print('   << output probabilities shape:' , prob_grid.get_shape())
    # print(prob.eval())

    #     eq = tf.equal(grid, pos)
    #     print( ' pos and grid probabalitiy matricies equal  -->', tf.reduce_all(eq).eval())
    trans_grid = tf.transpose(prob_grid,[2,3,0,1])
    
    #--------------------------------------------------------------------------------
    # kill distributions of NaN boxes (resulting from bboxes with height/width of zero
    # which cause singular sigma cov matrices
    #--------------------------------------------------------------------------------
    gauss_grid = tf.where(tf.is_nan(trans_grid),  tf.zeros_like(trans_grid), trans_grid)

    # gauss_tensor.append(trans_grid)
    print('   trans_grid shape: ', trans_grid.shape)

    class_inds = tf.to_int32(stacked_tensor[:,:,6])
    batch_grid, roi_grid = tf.meshgrid( tf.range(batch_size, dtype=tf.int32), tf.range(rois_per_image, dtype=tf.int32), indexing = 'ij' )

    print('   class shape     : ', class_inds.shape)
    # print(class_inds.eval())
    print('   roi_grid shape  : ', roi_grid.get_shape() )
    # print(roi_grid.eval())
    print('   batch_grid shape: ', batch_grid.get_shape())
    # print(batch_grid.eval())

    scatter_classes = tf.stack([batch_grid, class_inds, roi_grid ],axis = -1)
    print('   scatter_classes shape: ', scatter_classes.get_shape())
    print('   gaussian_grid        : ', gauss_grid.shape)    
    # print(scatter_classes.eval())

    gauss_scatt = tf.scatter_nd(scatter_classes, gauss_grid, [batch_size, num_classes, rois_per_image, img_w, img_h])
    print('   gaussian scattered   : ', gauss_scatt.shape)
    
    
    gauss_sum = tf.reduce_sum(gauss_scatt, axis=2)
    print('   gaussian_sum shape: ', gauss_sum.get_shape())    
    
    return  [gauss_sum, means, covar]
    

    
    
    

class PCNLayer(KE.Layer):
    """
    Receives the bboxes, their repsective classification and roi_outputs and 
    builds the per_class tensor

    Returns:
    -------
    The PCN layer returns the following tensors:

    pred_tensor :       [batch, NUM_CLASSES, TRAIN_ROIS_PER_IMAGE    , (index, class_prob, y1, x1, y2, x2, class_id, old_idx)]
                                in normalized coordinates
    pred_cls_cnt:       [batch, NUM_CLASSES] 
    
    gt_tensor:          [batch, NUM_CLASSES, DETECTION_MAX_INSTANCES, (index, class_prob, y1, x1, y2, x2, class_id, old_idx)]
    
    gt_cls_cnt:         [batch, NUM_CLASSES]
    
     Note: Returned arrays might be zero padded if not enough target ROIs.
    
    """

    def __init__(self, config=None, **kwargs):
        super().__init__(**kwargs)
        print('>>> PCN Layer : initialization')
        self.config = config

        
    def call(self, inputs):
        
        print('>>> PCN Layer : call ', type(inputs), len(inputs))
        print('     mrcnn_class.shape    :',  inputs[0].shape, type(inputs[0]))
        print('     mrcnn_bbox.shape     :',  inputs[1].shape, type(inputs[1])) 
        # print('     mrcnn_mask.shape     :',  inputs[2].shape, type(inputs[2])) 
        print('     output_rois.shape    :',  inputs[3].shape, type(inputs[3])) 
        # print('     gt_class_ids.shape   :',  inputs[4].shape, type(inputs[4])) 
        # print('     gt_bboxes.shape      :',  inputs[5].shape, type(inputs[5])) 
        mrcnn_class, mrcnn_bbox,  output_rois, gt_class_ids, gt_bboxes = inputs
        
        
        def wrapper(mrcnn_class, mrcnn_bbox, output_rois, gt_class_ids, gt_bboxes):

            # print('>>> PCN Layer Wrapper: call')
            pcn_tensor, pcn_cls_cnt = build_predictions(mrcnn_class, mrcnn_bbox,  output_rois, self.config)
            # print('pcn_tensor : ', pcn_tensor.shape)
            # print(pcn_tensor)
            # print('pcn_cls_cnt: ', pcn_cls_cnt.shape)
            # print(pcn_cls_cnt)
            
            print(' Build Gaussian np for detected rois =========================')    
            pcn_gaussian, means, covar = build_gaussian_np(pcn_tensor, pcn_cls_cnt, self.config)
            print('    means  : ', means.shape, '    covar : ', covar.shape)

            
            gt_tensor, gt_cls_cnt = build_ground_truth(gt_class_ids, gt_bboxes, self.config)
            
            # print(' gt_tensor  : ', gt_tensor.shape)
            # print( gt_tensor)
            # print(' gt_cls_cnt : ', gt_cls_cnt.shape)
            # print( gt_cls_cnt)
            
            print(' Build Gaussian np for ground_truth ==========================')    
            gt_gaussian, gt_means, gt_covar  = build_gaussian_np(gt_tensor , gt_cls_cnt, self.config)
            print('   gt means  : ', gt_means.shape, '   gt covar : ', gt_covar.shape)
            
            # pcn_tensor   = tf.convert_to_tensor(pred_tensor , name = 'pcn_tensor')
            # pcn_cls_cnt  = tf.convert_to_tensor(pred_cls_cnt, name = 'pcn_cls_cnt')
            # pcn_gaussian = tf.convert_to_tensor(res         , name = 'pcn_gaussian')
            # print('')
            # print('  pred_tensor ', type(pcn_tensor)  , pcn_tensor.shape ) 
            # print('  pred_cls_cnt', type(pcn_cls_cnt) , pcn_cls_cnt.shape) 
            # print('  pc_gaussian ', type(pcn_gaussian), pcn_gaussian.shape)
            # print('  gt_gaussian ', type(gt_gaussian) , gt_gaussian.shape)
            # print('  gt_tensor   ', type(gt_tensor)   , gt_tensor.shape  ) 
            # print('  gt_cls_cnt  ', type(gt_cls_cnt)  , gt_cls_cnt.shape )

            # # Stack detections and cast to float32
            # # TODO: track where float64 is introduced
            # detections_batch = np.array(detections_batch).astype(np.float32)
            # # Reshape output
            # # [batch, num_detections, (y1, x1, y2, x2, class_score)] in pixels
            # print('>>> PCN Layer Wrapper: end')
            return [pcn_gaussian, means      , covar    , 
                    gt_gaussian , gt_means   , gt_covar , 
                    pcn_tensor  , pcn_cls_cnt, gt_tensor, gt_cls_cnt]

            # return np.reshape(detections_batch, [self.config.BATCH_SIZE, self.config.DETECTION_MAX_INSTANCES, 6])

        # Return wrapped function
        # print('>>> PCN Layer : call end  ')

        return tf.py_func(wrapper, inputs, [tf.float32, tf.float32, tf.float32,
                                            tf.float32, tf.float32, tf.float32,
                                            tf.float32, tf.int16, 
                                            tf.float32, tf.int16])
        

        
    def compute_output_shape(self, input_shape):
        # may need to change dimensions of first return from IMAGE_SHAPE to MAX_DIM
        return [
            (None, self.config.NUM_CLASSES, self.config.IMAGE_SHAPE[0],self.config.IMAGE_SHAPE[1]),   
            (None, self.config.NUM_CLASSES, self.config.TRAIN_ROIS_PER_IMAGE, 2),       # pred_tensors 
            (None, self.config.NUM_CLASSES, self.config.TRAIN_ROIS_PER_IMAGE, 2),       # pred_tensors 

            (None, self.config.DETECTION_MAX_INSTANCES, self.config.IMAGE_SHAPE[0],self.config.IMAGE_SHAPE[1]),   
            (None, self.config.NUM_CLASSES, self.config.DETECTION_MAX_INSTANCES, 2),     # gt_tensors                         
            (None, self.config.NUM_CLASSES, self.config.DETECTION_MAX_INSTANCES, 2),     # gt_tensors                         
            
            
            (None, self.config.NUM_CLASSES, self.config.TRAIN_ROIS_PER_IMAGE, 8),       # pred_tensors 
            (None, self.config.NUM_CLASSES),                                            # pred_cls_cnt
            (None, self.config.NUM_CLASSES, self.config.TRAIN_ROIS_PER_IMAGE, 8),       # pred_tensors 
            (None, self.config.NUM_CLASSES),                                            # pred_cls_cnt
            (None, self.config.NUM_CLASSES, self.config.DETECTION_MAX_INSTANCES, 8),     # gt_tensors             
            (None, self.config.NUM_CLASSES)                                             # gt_cls_cnt
        ]

        
        
class PCNLayerTF(KE.Layer):
    '''
    Receives the bboxes, their repsective classification and roi_outputs and 
    builds the per_class tensor

    Returns:
    -------
    The PCN layer returns the following tensors:

    pred_tensor :       [batch, NUM_CLASSES, TRAIN_ROIS_PER_IMAGE    , (index, class_prob, y1, x1, y2, x2, class_id, old_idx)]
                                in normalized coordinates   
    pred_cls_cnt:       [batch, NUM_CLASSES] 
    gt_tensor:          [batch, NUM_CLASSES, DETECTION_MAX_INSTANCES, (index, class_prob, y1, x1, y2, x2, class_id, old_idx)]
    gt_cls_cnt:         [batch, NUM_CLASSES]
    
     Note: Returned arrays might be zero padded if not enough target ROIs.
    
    '''

    def __init__(self, config=None, **kwargs):
        super().__init__(**kwargs)
        print('>>> PCN Layer TF : initialization')
        self.config = config

        
    def call(self, inputs):
        
        print('>>> PCN Layer TF: call ', type(inputs), len(inputs))
        print('     mrcnn_class.shape    :',  inputs[0].shape, type(inputs[0]))
        print('     mrcnn_bbox.shape     :',  inputs[1].shape, type(inputs[1])) 
        print('     output_rois.shape    :',  inputs[2].shape, type(inputs[2])) 
        print('     gt_class_ids.shape   :',  inputs[3].shape, type(inputs[3])) 
        print('     gt_bboxes.shape      :',  inputs[4].shape, type(inputs[4])) 
        mrcnn_class, mrcnn_bbox,  output_rois, gt_class_ids, gt_bboxes = inputs

        pred_tensor , pred_cls_cnt  = build_predictions_tf(mrcnn_class, mrcnn_bbox, output_rois, self.config)
        gt_tensor   , gt_cls_cnt    = build_ground_truth_tf(gt_class_ids, gt_bboxes, self.config)  

        print(' Build Gaussian np for detected rois =========================')    
        pred_gaussian, means,covar  = build_gaussian_tf(pred_tensor, pred_cls_cnt, self.config)
        print('    means  : ', means.shape, '    covar : ', covar.shape)

        
        # print(' Build Gaussian np for ground_truth ==========================')    
        # gt_gaussian  = build_gaussian_np(gt_tensor , gt_cls_cnt, self.config)
        
        # pcn_tensor, pcn_cls_cnt = build_predictions(mrcnn_class, mrcnn_bbox,  output_rois, self.config)
        # print('pcn_tensor : ', pcn_tensor.shape)
        # print(pcn_tensor)
        # print('pcn_cls_cnt: ', pcn_cls_cnt.shape)
        # print(pcn_cls_cnt)       

        # print(' gt_tensor  : ', gt_tensor.shape,  gt_tensor, ' gt_cls_cnt : ', gt_cls_cnt.shape, gt_cls_cnt)
        
        
        # print('  pred_tensor ', type(pcn_tensor)  , pcn_tensor.shape ) 
        # print('  pred_cls_cnt', type(pcn_cls_cnt) , pcn_cls_cnt.shape) 
        # print('  pc_gaussian ', type(pcn_gaussian), pcn_gaussian.shape)
        # print('  gt_gaussian ', type(gt_gaussian) , gt_gaussian.shape)
        # print('  gt_tensor   ', type(gt_tensor)   , gt_tensor.shape  ) 
        # print('  gt_cls_cnt  ', type(gt_cls_cnt)  , gt_cls_cnt.shape )

        # # Stack detections and cast to float32
        # # TODO: track where float64 is introduced
        # detections_batch = np.array(detections_batch).astype(np.float32)
        # # Reshape output
        # # [batch, num_detections, (y1, x1, y2, x2, class_score)] in pixels
            # print('>>> PCN Layer Wrapper: end')
        return [ pred_gaussian, means, covar, pred_tensor, pred_cls_cnt, gt_tensor, gt_cls_cnt]

            # return np.reshape(detections_batch, [self.config.BATCH_SIZE, self.config.DETECTION_MAX_INSTANCES, 6])

        # Return wrapped function
        # print('>>> PCN Layer : call end  ')

        

        
    def compute_output_shape(self, input_shape):
        # may need to change dimensions of first return from IMAGE_SHAPE to MAX_DIM
        return [
            (None, self.config.NUM_CLASSES, self.config.IMAGE_SHAPE[0],self.config.IMAGE_SHAPE[1]),   # pred_gaussian
            (None, self.config.NUM_CLASSES, self.config.TRAIN_ROIS_PER_IMAGE, 2),                     # means
            (None, self.config.NUM_CLASSES, self.config.TRAIN_ROIS_PER_IMAGE, 2),                     # covar
            
            (None, self.config.NUM_CLASSES, self.config.TRAIN_ROIS_PER_IMAGE, 7),                     # pred_tensors 
            (None, self.config.NUM_CLASSES),                                                          # pred_cls_cnt
            (None, self.config.NUM_CLASSES, self.config.DETECTION_MAX_INSTANCES, 7),                  # GT_tensors 
            (None, self.config.NUM_CLASSES),                                                          # GT_cls_cnt

            ]
 
        

        
class PCILayer(KE.Layer):
    """
    Receives the bboxes, their repsective classification and roi_outputs and 
    builds the per_class tensor

    Returns:
    -------
    The PCI layer returns the following tensors:

    pred_tensor :       [batch, NUM_CLASSES, TRAIN_ROIS_PER_IMAGE    , (index, class_prob, y1, x1, y2, x2, class_id, old_idx)]
                                in normalized coordinates
    pred_gaussian       [batch, NUM_CLASSES, img_height, img_width ]
    pred_cls_cnt:       [batch, NUM_CLASSES] 
    
     Note: Returned arrays might be zero padded if not enough target ROIs.
    
    """

    def __init__(self, config=None, **kwargs):
        super().__init__(**kwargs)
        print('>>> PCI Layer : initialization')
        self.config = config

        
    def call(self, inputs):
        
        print('>>> PCI Layer : call')
        print('     mrcnn_class.shape    :',  inputs[0].shape, type(inputs[0]))
        print('     mrcnn_bbox.shape     :',  inputs[1].shape, type(inputs[1])) 
        print('     output_rois.shape    :',  inputs[2].shape, type(inputs[2])) 
    
        def wrapper(mrcnn_class, mrcnn_bbox, output_rois):
        
            pcn_tensor, pcn_cls_cnt , gt_tensor, gt_cls_cnt = \
                    build_predictions(mrcnn_class, mrcnn_bbox, output_rois, self.config)
            # print('pcn_tensor : ', pcn_tensor.shape)
            # print(pcn_tensor)
            # print('pcn_cls_cnt: ', pcn_cls_cnt.shape)
            # print(pcn_cls_cnt)
            print(' Build Gaussian np for detected rois =========================')    
            pcn_gaussian = build_gaussian_np(pcn_tensor, pcn_cls_cnt, self.config)

            return pcn_gaussian, pcn_tensor, pcn_cls_cnt

            # return np.reshape(detections_batch, [self.config.BATCH_SIZE, self.config.DETECTION_MAX_INSTANCES, 6])

        # Return wrapped function
        print('>>> PCN Layer : call end  ')

        return tf.py_func(wrapper, inputs, [tf.float32, tf.float32, tf.int16])
        

        
    def compute_output_shape(self, input_shape):
        # may need to change dimensions of first return from IMAGE_SHAPE to MAX_DIM
        return [
            (None, self.config.NUM_CLASSES, self.config.IMAGE_SHAPE[0],self.config.IMAGE_SHAPE[1]),     # pci_gaussian
            (None, self.config.NUM_CLASSES, self.config.DETECTION_MAX_INSTANCES, 8),                    # pci_tensors 
            (None, self.config.NUM_CLASSES),                                                            # pci_cls_cnt
        ]


def _old_build_gaussian_np_tf(in_tensor, in_cls_cnt, config):
    ## from scipy.stats import  multivariate_normal
    ## img_h, img_w = config.IMAGE_SHAPE[:2]
    ##num_images   = config.BATCH_SIZE
    ## num_classes  = config.NUM_CLASSES  
    

    for img in range(num_images):
        # print('====> Img: ', img)
        psx     = in_stacked[img]   #.eval(session = k_sess)  #     .eval(session=k_sess)
        
        # remove bboxes with zeros  
        # print('  input tensor shape _: ',psx.shape)            
        # print(psx)    
        ps = psx[~np.all(psx[:,2:6] == 0, axis=1)]
        # print('  input tensor after zeros removals shape _: ',ps.shape)            
        # print(ps)            

        width  = ps[:,5] - ps[:,3]
        height = ps[:,4] - ps[:,2]
        cx     = ps[:,3] + ( width  / 2.0)
        cy     = ps[:,2] + ( height / 2.0)
        mns    = np.stack((cx,cy),axis = -1)
        cov    = np.stack((width * 0.5 , height * 0.5), axis = -1)
        # weight = np.ones((ps.shape[0]))
        # print('cov ', cov)
        #--------------------------------------------------------------------------------
        # kill boxes with height/width of zero which cause singular sigma cov matrices
        # zero_boxes = np.argwhere(width+height == 0)
        # print('zero boxes ' , zero_boxes) 
        # cov[zero_boxes] = [1,1]
        # print('cov ', cov)
        # print(ps.shape, type(ps),width.shape, height.shape, cx.shape, cy.shape)
        
        # print('  img : ', img, ' means.shape:', means.shape, 'cov.shape ', cov.shape)
        rv  = list( map(multivariate_normal, mns, cov))
        # print('  size of rv is ', len(rv))
        pdf = list( map(lambda x,y: x.pdf(y) , rv, pos))
        # print('  size of pdf is ', len(pdf))
        # pdf_arr = np.asarray(pdf)       # PDF_ARR.SHAPE = # detection rois per image X  image_width X image_height
        pdf_arr = np.dstack(pdf)       # PDF_ARR.SHAPE = # detection rois per image X  image_width X image_height
        # print('  pdf_arr.shape : ' ,pdf_arr.shape)

        for cls in range(strt_cls, num_classes):
            _class_idxs = np.argwhere(ps[:,6] == cls).squeeze(axis=-1)
            # print('   *** img: ', img,' cls:',cls,' ',np.squeeze(_class_idxs))
            if _class_idxs.shape[0] == 0:
                continue
            ps_cls = ps[_class_idxs,:]

            ## build class specific mask based on bounding boxes
            # print('ps_cls : \n')
            # print(ps_cls)
            cls_boxes  = ps_cls[:,2:6]
            vld_boxes  = cls_boxes[~np.all(cls_boxes == 0, axis=1)]
            vld_boxes  = np.round(vld_boxes).astype(np.int)
            cls_mask.fill(0)
            
            # print('vld_boxes  \n' , vld_boxes)
            for i in vld_boxes:
                # _tmp[ slice(i[0],i[2]) , slice(i[1],i[3]) ]
                # print('(slice(',i[0],',',i[2],'), slice(',i[1],',',i[3],'))')
                # cls_mask[ slice(i[0],i[2]) , slice(i[1],i[3]) ] += 1
                cls_mask[ slice(i[0],i[2]) , slice(i[1],i[3]) ] = 1
                

            ##--------------------------------------------------
            norm   = np.sum(ps_cls[:,1])
            weight = ps_cls[:,1]/norm
            nones  = np.ones_like(weight)
            # print('    ps_cls shpe:',ps_cls.shape)            
            # print('    ps_cls :  \n', ps_cls)    
            
            # print('    Norm   :    ', norm)
            # print('    Weight shape', weight.shape, '  ', weight)
            # print('    pdf_arr[class,..] shape: ', pdf_arr[...,_class_idxs].shape)

            pdf_arr_abs = pdf_arr[...,_class_idxs]
            pdf_arr_wtd = pdf_arr[...,_class_idxs] * weight

            # print('    pdf_arr_wtd shape:       ', pdf_arr_wtd.shape)
            pdf_sum_wtd = np.sum(pdf_arr_wtd,axis=-1)
            pdf_sum_abs = np.sum(pdf_arr_abs,axis=-1)

            # print('    Weighted max/min ',np.max(np.max(pdf_arr_wtd),0),np.min(np.min(pdf_arr_wtd),0))
            # print('    Absolute max/min ',np.max(np.max(pdf_arr_abs),0),np.min(np.min(pdf_arr_abs),0))
            # print('    pdf_sum_wtd.shape ,: ' ,pdf_sum_wtd.shape , '   pdf_sum_abs.shape: ',pdf_sum_abs.shape)
            ###  print mask 
            # if rois_per_image == 100:
                # np.set_printoptions(threshold=99999, linewidth=2000)
                # print(np.array2string(cls_mask ,max_line_width=2000,separator=''))
            
            # Zout[img,cls] += np.sum(pdf_arr[_class_idxs],axis=0)[0]
            Zout[img,cls] = np.multiply(pdf_sum_abs, cls_mask) 

            
     
    
    
