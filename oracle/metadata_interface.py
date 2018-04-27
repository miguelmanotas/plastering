from mongoengine import *
pp = pprint.PrettyPrinter(indent=2)

connect('oracle')


# Data Models

class RawMetadata(Document):
    srcid = StringField(required=True)
    building = StringField(required=True)
    metadata = DictField()

class LabeledMetadata(Document):
    srcid = StringField(required=True)
    building = StringField(required=True)
    fullparsing = DictField()
    tagsets = ListField(StringField())
    point_tagset = StringField()


# Helper functions

def print_rawmetadata(srcid):
    obj = RawMetadata.objects(srcid=srcid)
    assert obj
    print(obj) # Or more fancier printing e.g., using pandas

def insert_groundtruth(srcid, fullparsing=None,
                    tagsets=None, point_tagset=None):
    obj = LabeledMetadata.objects(srcid=srcid)\
        .upsert_one(srcid=srcid, building=building)
    assert fullparsing or tagsets or point_tagset, 'WARNING:empty labels given'
    p.update(**{
            "set__name": "Hank",
            "set__address": "Far away"
    })

    new_labels = {}
    if fullparsing:
        new_labels['set__fullparsing'] = fullparsing
    if point_tagset:
        new_labels['set__point_tagset'] = point_tagset
    if tagsets:
        new_labels['set__tagsets'] = tagsets
    obj.update(**new_labels, upsert=True)
