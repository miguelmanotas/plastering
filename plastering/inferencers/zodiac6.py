import os
import sys
import importlib.util
import pdb
import time
import random
import re
from copy import deepcopy
import numpy as np
from functools import reduce
from collections import defaultdict
import arrow

import scipy
from scipy.cluster.vq import *
from scipy.cluster.hierarchy import linkage, dendrogram
import scipy.cluster.hierarchy as hier

from sklearn.feature_extraction.text import CountVectorizer
from sklearn.feature_extraction import DictVectorizer
from sklearn.preprocessing import LabelEncoder
from sklearn.ensemble import RandomForestClassifier
from sklearn.ensemble import BaggingClassifier
from sklearn.svm import OneClassSVM
from sklearn.neighbors import KNeighborsClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import AdaBoostClassifier
from sklearn.naive_bayes import MultinomialNB

from . import Inferencer
from ..metadata_interface import RawMetadata, LabeledMetadata
from ..common import POINT_TAGSET, adder
from ..rdf_wrapper.common import parse_srcid
from ..exceptions import AlgorithmError
from ..helpers import bidict

DEBUG = False
if DEBUG:
    np.set_printoptions(threshold=sys.maxsize)


def alphabet_tokenizer(s):
    return re.findall('[a-z]+', str(s).lower())


def meta_code_tokenizer(metadata_type):
    def code_tokenizer(s):
        if s == '':
            return []
        else:
            return re.findall('[a-zA-Z0-9]+', metadata_type + str(int(s)).lower())
    return code_tokenizer


def get_vectorizer(metadata_type):
    if metadata_type in ['BACnetUnit', 'BACnetType']:
        vectorizer = CountVectorizer(tokenizer=meta_code_tokenizer(metadata_type), lowercase=False)
    elif metadata_type=='Numeric':
        vectorizer= CountVectorizer()
    else:
        vectorizer = CountVectorizer(tokenizer=alphabet_tokenizer)
    return vectorizer


def isemptystr(s):
    if s:
        return False
    else:
        return True


def is_nonempty_item_included(l):
    if False in list(map(isemptystr, l)):
        return True
    else:
        return False

# class ZodiacInterface(Inferencer):
@Inferencer()
class ZodiacInterface(object):
    def __init__(self,
                 target_building,
                 target_srcids,
                 source_buildings=[],
                 config={},
                 **kwargs,
                 ):
        # init zodiac specific features
        self.required_label_types = [POINT_TAGSET]
        self.logger.info('Zodiac initiated')

        self.z=[]
        self.dists=[]

        self.cluster_map = []

        # init config file for Zodiac
        sample_num_list = config.get('sample_num_list', [0] * (len(source_buildings) + 1))
        # default values for sample_num_list are 0 samples from each building.

        if len(self.source_buildings) > len(sample_num_list):
            sample_num_list.append(0)

        # get srcids from other buildings
        source_buildings_srcids = []
        for source_building, sample_num in zip(source_buildings,
                                               sample_num_list):
            objects = self.query_labels(building=source_building)
            source_srcids = random.sample(
                [obj.srcid for obj in objects], sample_num)
            source_buildings_srcids += source_srcids

        # Pick srcids
        self.total_srcids = deepcopy(target_srcids) + source_buildings_srcids
        self.available_srcids = deepcopy(self.training_srcids)

        self.init_model()

    def init_model(self):

        # Init buffers
        self.true_labels = {}
        self.training_labels = []
        self.trained_cids = []

        # Init thresholds
        self.thresholds = [(0.1, 0.95), (0.1, 0.9), (0.15, 0.9), (0.15, 0.85),
                           (0.2, 0.85), (0.25, 0.85), (0.3, 0.85), (0.35, 0.85),
                           (0.4, 0.85), (0.45, 0.85), (0.5, 0.85), (0.55, 0.85),
                           (0.6, 0.85), (0.65, 0.85), (0.7, 0.85), (0.75, 0.85),
                           (0.8, 0.85), (0.84999999, 0.85)]
        self.th_ptr = 0
        self.th_min, self.th_max = self.thresholds[self.th_ptr]

        self.counter=0
        self.counter_vector=[]
        self.available_vector=[]

        # Init ML model.
        self.model = RandomForestClassifier(
            n_estimators=self.config.get('n_estimators', 400),
            random_state=self.config.get('random_state', 0),
            n_jobs=self.config.get('n_jobs', 6),
        )

        # Init raw data for Zodiac
        raw_metadata = defaultdict(dict)
        for srcid in self.total_srcids:
            raw_point = RawMetadata.objects(srcid=srcid).first()
            assert raw_point['metadata'], 'Raw metadata for {0} does not exist'.format(srcid)
            for metadata_type in self.valid_metadata_types:
                raw_metadata[metadata_type][srcid] = raw_point.metadata.get(metadata_type, None)
        
        self.raw_metadata=raw_metadata

        self.total_bow = self.init_bow(self.total_srcids, raw_metadata)
        target_bow = self.get_sub_bow(self.target_srcids)
        self.cluster_map = self.create_cluster_map(target_bow, self.target_srcids)
        self.training_labels += [self.query_labels(srcid=srcid).first().point_tagset
                                 for srcid in self.available_srcids]

    def update_thresholds(self):
        self.th_ptr += 1
        self.th_min, self.th_max = self.thresholds[self.th_ptr]

    def get_random_learning_srcids(self, sample_num):
        srcids = []
        random.seed(42)
        random_cids = random.sample(self.cluster_map.keys(), sample_num)
        for c_id in random_cids:
            random.seed(42)
            srcid = random.choice(self.cluster_map[c_id])
            srcids.append(srcid)
        return srcids

    def vectorize(self, d, srcids, vectorizer):
        data = [d[srcid] for srcid in srcids]
        if is_nonempty_item_included(data):
            vect = vectorizer.fit_transform(data).toarray()
            return vect
        else:
            return None

    def init_bow(self, srcids, raw_metadata):
        vectors = [self.vectorize(raw_metadata[metadata_type],
                                  srcids,
                                  get_vectorizer(metadata_type),
                                  )
                   for metadata_type in self.valid_metadata_types]
        bow = np.hstack([vect for vect in vectors if isinstance(vect, np.ndarray)])
        return bow

    def create_cluster_map(self, bow, srcids):
        cluster_map = {}
        z = linkage(bow, metric='cityblock', method='complete')
        self.z=z
        dists = list(set(z[:, 2]))
        self.dists=dists
        thresh = (dists[2] + dists[3]) / 2
        self.logger.info('Threshold: {0}'.format(thresh))
        b = hier.fcluster(z, thresh, criterion='distance')
        assert bow.shape[0] == len(b)
        assert len(b) == len(srcids)
        for cid, srcid in zip(b, srcids):
            cluster_map[cid] = cluster_map.get(cid, []) + [srcid]

        self.logger.info('# of clusters -corrected-: {0}'.format(len(cluster_map)))
        self.logger.info('sizes of clusters:{0}'.format(sorted(map(len, cluster_map.values()))))
	

        return cluster_map

    def find_cluster_id(self, srcid):
        for cid, srcids in self.cluster_map.items():
            if srcid in srcids:
                return cid
        raise Exception('Srcid not found in the cluster map: {0}'
                        .format(srcid))

    def get_sub_bow(self, srcids):
        return self.total_bow[
            [self.total_srcids.index(srcid) for srcid in srcids]
        ]

    def add_cluster_label(self, cid, label):
        if cid in self.trained_cids:
            self.logger.warning('Cluster already learned: {0}'.format(cid))
            return None
        self.trained_cids.append(cid)
        cluster_srcids = self.cluster_map[cid]
        for srcid in cluster_srcids:
            if srcid in self.available_srcids:
                self.logger.debug('already there')
        self.available_srcids += cluster_srcids
        self.training_labels += [label] * len(cluster_srcids)
        if DEBUG:
            for srcid in cluster_srcids:
                labeled_doc = LabeledMetadata.objects(srcid=srcid)[0]
                true_label = labeled_doc.point_tagset
                if true_label != label:
                    self.logger.debug('At {0}, pred({1}) != true({2})'
                                      .format(srcid, label, true_label))
                    cluster_all_labels = [LabeledMetadata.objects(srcid=srcid)[0].point_tagset
                                          for srcid in cluster_srcids]
                    self.logger.debug('There are {0} labels here'
                                      .format(len(set(cluster_all_labels))))

    def calc_prior_g_acc(self):
        cnt = 0
        acc = 0
        for triple, confidence in self.prior_confidences.items():
            cnt += 1
            srcid = triple[0].split('#')[-1]
            tagset = triple[2].split('#')[-1]
            true_tagset = self.query_labels(srcid=srcid).first().point_tagset
            if tagset == true_tagset:
                acc += 1
        if cnt:
            acc = 0 if not cnt else acc / cnt
            self.logger.info('Prior graph\'s Accuracy: {0}'.format(acc))

    def apply_prior_augment_samples(self):
        prior_preds = {}
        if self.prior_g:
            self.calc_prior_g_acc()
            for triple, confidence in self.prior_confidences.items():
                if confidence > self.th_max:  # If the prediction is confident
                    srcid = triple[0].split('#')[-1]
                    tagset = triple[2].split('#')[-1]
                    if srcid in self.target_srcids:
                        prior_preds[srcid] = tagset
        return prior_preds

    def update_model(self, new_srcids):
        # Add new srcids into the training set.
        for srcid in new_srcids:
            labeled = self.query_labels(srcid=srcid)
            if not labeled:
                raise Exception('Labels do not exist for {0}'.format(srcid))
            labeled = labeled[0]
            point_tagset = labeled.point_tagset
            if not point_tagset:
                raise Exception('Point Tagset not found at {0}: {1}'
                                .format(srcid, labeled.tagsets))
            self.true_labels[srcid] = point_tagset

        for srcid in new_srcids:
            cid = self.find_cluster_id(srcid)
            cluster_label = self.true_labels[srcid]
            self.add_cluster_label(cid, cluster_label)
        if self.learn_model():
            self.model_initiated = True
        self.select_informative_samples(1)

        prior_preds = self.apply_prior_augment_samples()
        for srcid, point_tagset in prior_preds.items():
            if srcid not in self.available_srcids:
                cid = self.find_cluster_id(srcid)
                cluster_label = point_tagset
                self.add_cluster_label(cid, cluster_label)
        if prior_preds:
            self.learn_model()
            self.select_informative_samples(1)

    def select_srcid_per_cluster(self, srcids):
        cids = []
        for srcid in srcids:
            srcid_handled = False
            for cid, cluster in self.cluster_map.items():
                if srcid in cluster:
                    if cid not in self.trained_cids:
                        cids.append(cid)
                    srcid_handled = True
                    break
            assert srcid_handled, "{0}'s cluster is not found".format(srcid)
        new_srcids = []
        cids = list(set(cids))
        cluster_sizes = [len(self.cluster_map[cid]) for cid in cids]
        for cid in cids:
            random.seed(42)
            new_srcids.append(random.choice(self.cluster_map[cid]))
        new_srcids = [row[1] for row in sorted(zip(cluster_sizes, new_srcids),
                                               reverse=True)]
        return new_srcids

    def apply_prior_quiver(self, pred, target_srcids):
        if not self.prior_g:
            return []

        # If points in a vav are identified same,
        # remove it from identified list.
        vavs = self.prior_g.get_vavs()
        cand_srcids = []
        for vav in vavs:
            points = self.prior_g.get_vav_points(vav)
            point_types = defaultdict(list)
            for point in points:
                srcid = point.split('#')[-1]
                if srcid in target_srcids:
                    point_idx = target_srcids.index(srcid)
                    pred_type = pred[point_idx]
                    point_types[pred_type].append(point)
            for point_type, points in point_types.items():
                if len(points) > 2:
                    cand_srcids += [parse_srcid(point) for point in points]
        new_srcids = self.select_srcid_per_cluster(cand_srcids)
        return new_srcids

    def select_informative_samples(self, sample_num=1):
        new_srcids = []
        tot_srcids = reduce(adder, self.cluster_map.values())
        base_sample_bow = self.get_sub_bow(tot_srcids)
        base_pred_labels = self.model.predict(base_sample_bow)
        new_srcids = self.apply_prior_quiver(base_pred_labels, tot_srcids)
        new_srcids = new_srcids[0:sample_num]

        test_flag = 0
        looping_flag = False
        while len(self.available_srcids) != len(self.total_srcids) and  \
                len(new_srcids) < sample_num:
            self.learn_model()
            th_update_flag = True
            prev_available_srcids = deepcopy(self.available_srcids)
            self.logger.info('curr available srcids: {0}'.format(len(prev_available_srcids)))
            for cid, cluster_srcids in self.cluster_map.items():
                if cid in self.trained_cids:
                    continue
                sample_bow = self.get_sub_bow(cluster_srcids)
                confidence = self.model.predict_proba(sample_bow)
                pred_labels = self.model.predict(sample_bow)
                max_confidence = 0
                max_confidence = max(map(max, confidence))

                if max_confidence >= self.th_min and max_confidence < self.th_max:  # Gray zone
                    pass
                elif max_confidence >= self.th_max:
                    if looping_flag:
                        raise AlgorithmError(self, 'infinite loop found.')
                    th_update_flag = False
                    test_flag = cluster_srcids
                    self.trained_cids.append(cid)
                    self.available_srcids += cluster_srcids
                    self.training_labels += pred_labels.tolist()
                    # Check true label for debugging
                    if DEBUG:
                        for srcid, pred_label in zip(cluster_srcids,
                                                     pred_labels):
                            labeled_doc = LabeledMetadata.objects(srcid=srcid)[0]
                            true_label = labeled_doc.point_tagset
                            if true_label != pred_label:
                                self.logger.debug('At {0}, pred({1}) != true({2})'
                                                  .format(srcid, pred_label, true_label))
#                    break #TODO: Check if it works.
                elif max_confidence < self.th_min:
                    if looping_flag:
                        raise AlgorithmError(self, 'infinite loop found.')
                    test_flag = 2
                    random.seed(42)
                    new_srcids.append(random.choice(cluster_srcids))
                    th_update_flag = False
                    #self.logger.info('srcid added')
                    #self.counter+=1
                    #self.logger.info('Number of requested srcids {0}'.format(self.counter))
                    #self.counter_vector.append(self.counter)
                    #self.available_vector.append(len(self.available_srcids))


                    if len(new_srcids) == sample_num:
                        break

            if th_update_flag:
                self.logger.info('The threshold is updated')
                self.update_thresholds()
            else:
                if len(new_srcids) > 0:
                    reason = 'new srcids are found: {0}'.format(len(new_srcids))
                elif len(self.available_srcids) > len(prev_available_srcids):
                    reason = 'increased srcids: {0}'.format(
                        len(self.available_srcids) - len(prev_available_srcids))
                    if DEBUG:
                        presumed_srcids = [srcid for srcid in self.available_srcids
                                           if srcid not in prev_available_srcids]
                        presumed_cids = list(set([self.find_cluster_id(srcid)
                                                  for srcid in presumed_srcids]))
                        for cid in presumed_cids:
                            label = LabeledMetadata.objects(srcid=self.cluster_map[cid][0]
                                                            )[0].point_tagset
                            if label not in self.true_labels.values():
                                self.logger.debug('Presumed "{0}" is not in trained labels'
                                                  .format(label))
                else:
                    reason = 'test flag: {0}'.format(test_flag)
                    looping_flag = True
                self.logger.info('The threshold is not updated because {0}'.format(reason))

            self.logger.info('Current threshold pointer: {0}/{1}'.format(self.th_ptr,
                                                                         len(self.thresholds)))
        return new_srcids

    def get_num_sensors_in_gray(self):
        # TODO: This line should consider source building srcids"
        return len(self.target_srcids) - len(self.available_srcids)

    def learn_auto(self, iter_num=-1, inc_num=1, evaluate_flag=True):
        gray_num = 1000
        cnt = 0
        seed_sample_num = 8
        while (iter_num == -1 and gray_num > 0) or cnt < iter_num:
            self.logger.eval('--------------------------')
            self.logger.eval('{0}th iteration'.format(cnt))
            self.learn_model()
            if self.model_initiated:
                new_sample_num = 1
            else:
                new_sample_num = seed_sample_num
            new_srcids = self.select_informative_samples(new_sample_num)
            self.update_model(new_srcids)
            gray_num = self.get_num_sensors_in_gray()
            if evaluate_flag:
                self.evaluate(self.target_srcids)
                self.logger.eval('f1: {0}'.format(self.history[-1]['metrics']['f1']))
                self.logger.eval('macrof1: {0}'.format(self.history[-1]['metrics']['macrof1']))
            self.logger.info('curr new srcids: {0}'.format(len(new_srcids)))
            if new_srcids:
                self.logger.info("new cluster's size: {0}"
                                 .format(len(self.cluster_map[self.find_cluster_id(
                                     new_srcids[0])])))
            self.logger.info('gray: {0}/{1}'.format(gray_num, len(self.target_srcids)))
            self.logger.info('training srcids: {0}'.format(len(self.training_srcids)))
            cnt += 1
            self.counter_vector.append(cnt)
            self.available_vector.append(len(self.available_srcids))
            #self.logger.info('srcid added')
                    #self.counter+=1
                    #self.logger.info('Number of requested srcids {0}'.format(self.counter))
            
        self.learn_model()

    def learn_model(self):
        if not self.available_srcids:
            self.logger.warning('not learning anything due to the empty training data')
            return False
        self.training_bow = self.get_sub_bow(self.available_srcids)
        self.model.fit(self.training_bow, self.training_labels)
        return True

    def predict(self, target_srcids=None, output_format='ttl'):
        t0 = arrow.get()
        if not target_srcids:
            target_srcids = self.target_srcids

        self.learn_model()
        pred_confidences = {}
        pred_g = self.new_graph()
        sample_bow = self.get_sub_bow(target_srcids)

        pred_points = self.model.predict(sample_bow)
        confidences = self.model.predict_proba(sample_bow)
        for srcid, pred_point, prob in zip(target_srcids,
                                           pred_points,
                                           confidences):
            prob = max(prob)
            self.add_pred(pred_g, pred_confidences, srcid, pred_point, prob)
        self.pred_g = pred_g
        self.pred_confidences = pred_confidences
        t1 = arrow.get()
        self.logger.debug('REALLY it takes this: {0}'.format(t1 - t0))
        if output_format == 'ttl':
            return pred_g
        elif output_format == 'json':
            return pred_points

    def predict_proba(self, target_srcids=None, output_format='ttl', *args, **kwargs):
        res = self.predict(target_srcids, output_format=output_format)
        return res, self.pred_confidences
