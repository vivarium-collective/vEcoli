import copy

from bigraph_schema import deep_merge


def dict_union(a: dict, b: dict, mutate_a: bool = False, secure: bool = False) -> dict:
    """
    Performs `bigraph_schema.deep_merge(a, b)` but returns a new object rather than mutating `a` if
    and only if `mutate_a = True`, otherwise performs a regular call to `deep_merge`. If `secure` is `True`,
    then both `a` and `b` will be explicitly deleted from memory, leaving only this return.
    """
    if not mutate_a:
        a = copy.deepcopy(a)
    c = deep_merge(a, b)
    if secure:
        del a
        del b
    return c
