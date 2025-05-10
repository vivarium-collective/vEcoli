import abc

from process_bigraph import ProcessTypes


CORE = ProcessTypes()


class MetaABCAndType(abc.ABCMeta, type):
    pass

