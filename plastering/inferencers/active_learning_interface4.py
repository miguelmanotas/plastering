import numpy as np
import re
import pdb

from collections import defaultdict as dd

from sklearn.feature_extraction.text import CountVectorizer as CV
from sklearn.preprocessing import LabelEncoder as LE

from . import Inferencer
from .algorithm.active_learning import active_learning
from ..metadata_interface import *


def get_name_features(names):
    name = []
    for i in names:
        s = re.findall('(?i)[a-z]{2,}',i)
        name.append(' '.join(s))
    cv = CV(analyzer='char_wb', ngram_range=(3,4))
    fn = cv.fit_transform(name).toarray()
    return fn

@Inferencer()
#class ActiveLearningInterface(Inferencer):
class ActiveLearningInterface(object):

    def __init__(self,
                 target_building,
                 target_srcids,
                 #folder,
                 fold,
                 #rounds,
                 #n_cluster,
                 pgid=None,
                 use_all_metadata=False,
                 source_building=None,
                 **kwargs,
                 ):
        
        #self.folds=folds
        
        self.required_label_types = [POINT_TAGSET]
        srcids = [point['srcid'] for point in query_labels(pgid=pgid, building=target_building)]
        pt_type = [query_labels(pgid=pgid, srcid=srcid).first().point_tagset for srcid in srcids]
        self.gt=pt_type
        if use_all_metadata:
            pt_name = []
            for srcid in srcids:
                raw_metadata = RawMetadata.objects(srcid=srcid).first().metadata
                sentence = []
                sentence = '\n'.join([raw_metadata.get(metadata_type, '')
                                      for metadata_type
                                      in ['VendorGivenName',
                                          'BACnetName',
                                          'BACnetDescription']
                                      ])
                pt_name.append(sentence)
        else:
            pt_name = [RawMetadata.objects(srcid=srcid).first()
                       .metadata['VendorGivenName'] for srcid in srcids]

        fn = get_name_features(pt_name)
        #j=[]
        #for i in range(len(fn)):
        #    j.append([2])
        #fn=np.append(fn,j,axis=1)

        le = LE()
        try:
            le.fit(pt_type)
        except:
            pdb.set_trace()

        transfer_fn = []
        transfer_label = []

        if source_building:
            srcids = [point['srcid'] for point
                      in query_labels(pgid=pgid, building=source_building)]
            source_type = [query_labels(pgid=pgid, srcid=srcid).first().point_tagset
                       for srcid in srcids]
            source_name = [RawMetadata.objects(srcid=srcid).first()\
                       .metadata['VendorGivenName'] for srcid in srcids]

            fn_all = get_name_features( pt_name + source_name )
            fn = fn_all[:len(pt_name), :]
            transfer_fn = fn_all[len(pt_name):, :]

            try:
                le.fit( pt_type + source_type )
                transfer_label = le.transform(source_type)
            except:
                pdb.set_trace()

            print ('%d instances loaded from transferred bldg: %s'%(len(transfer_label), source_building))

        try:
            label = le.transform(pt_type)
        except:
            pdb.set_trace()

        #print ('# of classes is %d'%len(np.unique(label)))
        print ('running active learning by Hong on building %s'%target_building)
        print ('%d instances loaded'%len(pt_name))

        self.fold=[]
        self.rounds=[]
        #self.n_cluster=n_cluster
        self.fn=fn
        self.label=label
        self.transfer_fn=transfer_fn
        self.transfer_label=transfer_label
        self.min=[]


        # self.learner = active_learning(
        #     #fold=10,
        #     fold=2,
        #     #rounds=100,
        #     rounds=rounds,
        #     #2 * len( np.unique(label) ),
        #     #n_cluster=28,
        #     n_cluster=n_cluster,
        #     fn=self.fn,
        #     label=label,
        #     transfer_fn=transfer_fn,
        #     transfer_label=transfer_label
        # )


    def example_set():
        #TODO: get a set of example IDs that the user can provide label for, i.e, the set of examples to run AL
        pass


    def get_label(idx):
        #TODO: get the label for the example[idx] from human
        pass


    def select_example(self):

        idx, c_idx = self.learner.select_example()

        return idx


    def update_model(self, srcid, cluster_id):

        self.learner.labeled_set.append(srcid)
        self.learner.new_ex_id = srcid
        self.learner.cluster_id = cluster_id
        self.learner.update_model()


    def predict(self, target_srcids):

        return self.learner.clf.predict(target_srcids)


    def learn_auto(self):

        self.learner.run_CV()

