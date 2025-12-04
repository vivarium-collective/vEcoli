import _pickle as pickle
import sys
import os
import json
from pathlib import Path
from pprint import pformat, pprint
import numpy as np
from numpy.dtypes import Int64DType as I64Dtype
from numpy.dtypes import Float64DType as F64Dtype
from numpy import int64 as I64
from numpy import int32 as I32
from numpy import float64 as F64
import sympy as sp
from sympy.matrices.dense import MutableDenseMatrix as MDM
from scipy.sparse._csc import csc_matrix as CSC
from scipy.sparse._csr import csr_matrix as CSR
from unum import Unum
from sympy.core.add import Add
from sympy.core.mul import Mul
from sympy.core import numbers
from time import time
from Bio.Seq import Seq
import argparse
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from wholecell.utils.filepath import ROOT_PATH


REGISTRY_DIR = "out/parameter_registry"

if __name__ == "__main__":
    parse = argparse.ArgumentParser()
    parse.add_argument("--outpath", default=None)
    parse.add_argument("--id")
    args = parse.parse_args()

    dataset_id = args.id
    dataset_dir = Path(f"{REGISTRY_DIR}/{dataset_id}")

    pickled_file = dataset_dir / "simData.cPickle"
    if not pickled_file.exists():
        raise IOError(f"The file {pickled_file!s} does not yet exist")

    with open(pickled_file, 'rb') as file:
        unpick = pickle.Unpickler(file)
        data = unpick.load()


    class example:
        def __init__(self, p1, p2):
            self.p1 = p1
            self.p2 = p2

    class child:
        def __init__(self, num):
            self.num = num


    def object_traversal(obj):
        types = (float, int, str, bool, bytes)
        result = dict()
        if obj.__class__.__name__ in dir(numbers):
            #A bit messy
            result = obj.numerator/obj.denominator
        elif isinstance(obj, (np.ndarray, np.matrix, np.str_, sp.NDimArray, sp.Matrix, MDM)):
            result = f"{type(obj).__name__} of length {len(obj)} and shape {obj.shape}"
        elif isinstance(obj, (I64, F64, I32)):
            result = obj.tolist()
        elif isinstance(obj, Add):
            result = repr(obj)
        elif isinstance(obj, (CSC, CSR)):
            #TODO figure out a way to represent this matrix without actually printing its entire data
            result =  f"SPARSE MATRIX with shape {obj.shape}"
        elif isinstance(obj, (I64Dtype, F64Dtype)):
            result = obj.num
        elif callable(obj):
            #TODO figure out a way to describe the method
            ourdoc = obj.__doc__.replace("\n", "").replace("    ", " ").replace("   ", " ").replace("  ", " ") if obj.__doc__ else None
            result = f"METHOD: {ourdoc}"
        elif isinstance(obj, Unum):
            result =  f"{obj._value} {list(obj._unit.keys())[0]}"
        elif isinstance(obj, dict):
            #if len(obj) > 100
            try:
                result = f"DICT of length {len(obj)} with {type(list(obj.keys())[0]).__name__.upper()}, {type(list(obj.values())[0]).__name__.upper()} key, value pairs."
            except IndexError:
                result = "EMPTY DICT"
            # else:
            #     for k, v in obj.items():
            #         if isinstance(k, I64):
            #             result[k.tolist()] = object_traversal(v)
            #         elif isinstance(k, tuple):
            #             result["tuple"] = object_traversal(v)
            #         else:
            #             result[k] = object_traversal(v)
        elif isinstance(obj, list):
            #TODO express the list without printing its entire data
            try:
                result = f"LIST of length {len(obj)} of {type(obj[0]).__name__.upper()} types."
            except IndexError:
                result = "EMPTY LIST"
        elif isinstance(obj, tuple):
            try:
                result = f"TUPLE of length {len(obj)} of {type(obj[0]).__name__.upper()} types."
            except IndexError:
                result = "EMPTY TUPLE"
        elif isinstance(obj, set):
            if len(obj) == 0:
                result = "EMPTY SET"
            else:
                for element in obj:
                    ourtype = type(element).__name__.upper()
                result = f"SET of length {len(obj)} of {ourtype} types."
        elif isinstance(obj, Seq):
            result["Sequence"] = object_traversal(obj._data)
        elif isinstance(obj, types):
            if isinstance(obj, bytes):
                if len(obj.decode()) > 10000:
                    result = obj.decode()[:1000] + "..."
                else:
                    result = obj.decode()
            else:
                result = obj
        else:
            for key in dir(obj):
                if not key.startswith("__"):
                    value = getattr(obj, key)
                    result[key] = object_traversal(value)
        return result

    exobj = example(child(2), child(3))


    def gothrough(obj, beento=set()):
        if isinstance(obj, dict):
            for k, v in obj.items():
                beento.union(gothrough(k, beento))
                beento.union(gothrough(v, beento))
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                beento.add(gothrough(v, beento))
        elif not isinstance(obj, (int, str, bool, float)):
            beento.add(type(obj))
        return beento

    simdict = object_traversal(data)

    # print(gothrough(simdict))

    try:
        with open(args.outpath or dataset_dir / "schema.json", "w") as file:
            json.dump(simdict, file, indent=4)
    except TypeError as r:
        print(r)
