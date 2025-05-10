# -- unum -- #
import unum as unum

from wholecell.utils import units



__all__ = [
    "type_name", "apply_unum", "check_unum", "divide_unum", "serialize_unum", "deserialize_unum", "default"
]


def type_name():
    return "unum"


def default():
    return 1 * units.s / units.mol


def apply_unum(schema, current, update, top_schema, top_state, path, core):
    return current + update


def check_unum(schema, state, core):
    return isinstance(state, unum.Unum)   


def divide_unum(schema, state, values, core):
    divs = values.get('divisions', 2)
    portion = state / divs 
    return [
        portion for _ in range(divs)
    ] 


def serialize_unum(schema, value, core):
    return str(value)  


def deserialize_unum(schema, encoded: str, core):
    if isinstance(encoded, unum.Unum):
        return encoded
    else:
        try:
            parts: list[str] = encoded.split(' ')
            magnitude = eval(parts[0])
            units = eval(parts[1].replace('[', '').replace(']', ''))
            return magnitude * units
        except: 
            raise ValueError("could not parse unum")


# example
avogadro = 6.02214076e23 / units.mol