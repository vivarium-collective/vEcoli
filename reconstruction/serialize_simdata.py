from typing import Any
import importlib
import re
import numpy as np
import orjson
from unum import Unum
from unum.units import *  # noqa: F401, F403 - needed for unit reconstruction

from reconstruction.ecoli.simulation_data import SimulationDataEcoli
from pathlib import Path
from ecoli.library.sim_data import LoadSimData


DEFAULT_EXPORT_PATH = Path("reconstruction") / "sim_data" / "simdata.json"

# Tag used to mark typed values in serialized output
_TYPE_TAG = "__type__"
_VALUE_TAG = "__value__"
_DTYPE_TAG = "__dtype__"
_SHAPE_TAG = "__shape__"
_UNIT_TAG = "__unit__"


class SimulationParameterDataset:
    sim_data: SimulationDataEcoli

    def __init__(self, simdata_path: Path | None = None) -> None:
        if simdata_path is not None:
            self.sim_data = load_simdata(simdata_path)

    def serialize(self) -> bytes:
        return orjson.dumps(serialize(self.sim_data))

    def export(self, fp: Path | None = None) -> bytes:
        serialized = self.serialize()
        with open(fp or DEFAULT_EXPORT_PATH, "wb") as f:  # note: "wb" not "w"
            f.write(serialized)
        return serialized

    @classmethod
    def from_serialized(
        cls,
        data: bytes | str | dict,
        reconstruct_classes: bool = True,
    ) -> "SimulationParameterDataset":
        """Deserialize and hydrate from serialized JSON data.

        Args:
            data: Either raw JSON bytes/string, or an already-parsed dict.
            reconstruct_classes: If True (default), reconstruct original class
                instances. If False, return nested dicts instead.

        Returns:
            A SimulationParameterDataset with hydrated simdata as a
            SimulationDataEcoli instance (or dict if reconstruct_classes=False).
        """
        instance = cls(simdata_path=None)
        if isinstance(data, bytes):
            parsed = orjson.loads(data)
        elif isinstance(data, str):
            parsed = orjson.loads(data.encode())
        else:
            parsed = data
        instance.sim_data = deserialize(parsed, reconstruct_classes=reconstruct_classes)
        return instance

    @classmethod
    def load(
        cls,
        fp: Path | None = None,
        reconstruct_classes: bool = True,
    ) -> "SimulationParameterDataset":
        """Load and deserialize from a JSON file.

        Args:
            fp: Path to the JSON file. Defaults to DEFAULT_EXPORT_PATH.
            reconstruct_classes: If True (default), reconstruct original class
                instances. If False, return nested dicts instead.

        Returns:
            A SimulationParameterDataset with hydrated simdata.
        """
        with open(fp or DEFAULT_EXPORT_PATH, "rb") as f:
            data = f.read()
        return cls.from_serialized(data, reconstruct_classes=reconstruct_classes)


def load_simdata(simdata_path: Path) -> SimulationDataEcoli:
    return LoadSimData(sim_data_path=simdata_path.__str__()).sim_data


def serialize(obj: Any) -> Any:
    """Serialize with type tags for lossless round-tripping."""
    if isinstance(obj, Unum):
        # Extract numeric value and unit string
        raw_value = obj.asNumber()
        if isinstance(raw_value, np.ndarray):
            value = serialize(raw_value.tolist())
        elif isinstance(raw_value, np.generic):
            value = raw_value.item()
        else:
            value = raw_value
        unit_str = str(obj.strUnit()).strip()
        return {
            _TYPE_TAG: "unum",
            _VALUE_TAG: value,
            _UNIT_TAG: unit_str,
        }
    if isinstance(obj, np.ndarray):
        return {
            _TYPE_TAG: "ndarray",
            _VALUE_TAG: serialize(obj.tolist()),
            _DTYPE_TAG: str(obj.dtype),
            _SHAPE_TAG: list(obj.shape),
        }
    if isinstance(obj, np.generic):
        return {
            _TYPE_TAG: "np_scalar",
            _VALUE_TAG: obj.item(),
            _DTYPE_TAG: str(obj.dtype),
        }
    if isinstance(obj, np.dtype):
        return {_TYPE_TAG: "dtype", _VALUE_TAG: str(obj)}
    if isinstance(obj, set):
        return {_TYPE_TAG: "set", _VALUE_TAG: [serialize(item) for item in obj]}
    if isinstance(obj, frozenset):
        return {_TYPE_TAG: "frozenset", _VALUE_TAG: [serialize(item) for item in obj]}
    if isinstance(obj, tuple):
        return {_TYPE_TAG: "tuple", _VALUE_TAG: [serialize(item) for item in obj]}
    if isinstance(obj, dict):
        return {str(k): serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [serialize(item) for item in obj]
    if hasattr(obj, "__dict__") and not isinstance(obj, type):
        return {
            _TYPE_TAG: "object",
            "__class__": f"{obj.__class__.__module__}.{obj.__class__.__name__}",
            _VALUE_TAG: {k: serialize(v) for k, v in obj.__dict__.items()},
        }

    # Fallback: if it's not a basic JSON type, stringify it
    if not isinstance(obj, (str, int, float, bool, type(None))):
        return {_TYPE_TAG: "str_fallback", _VALUE_TAG: str(obj)}

    return obj


def _import_class(class_path: str) -> type:
    """Dynamically import a class from its fully qualified path."""
    module_path, class_name = class_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


# Track which classes we've already warned about to avoid spam
_warned_classes: set[str] = set()


def deserialize(obj: Any, reconstruct_classes: bool = False) -> Any:
    """Deserialize tagged values back to their original types.

    Args:
        obj: The object to deserialize (typically parsed JSON).
        reconstruct_classes: If True, reconstruct original class instances
            from serialized objects. If False, return dicts instead.

    Returns:
        The deserialized object with original types restored.
    """
    if isinstance(obj, dict):
        type_tag = obj.get(_TYPE_TAG)

        if type_tag == "unum":
            value = deserialize(obj[_VALUE_TAG], reconstruct_classes)
            # Convert list back to array if needed
            if isinstance(value, list):
                value = np.array(value)
            unit_str = obj.get(_UNIT_TAG, "")
            if unit_str:
                # Parse unit string and reconstruct Unum
                # Unit strings look like "[kg]", "[m/s]", etc.
                unit_match = re.match(r"\[(.+)]", unit_str)
                if unit_match:
                    unit_expr = unit_match.group(1)
                    try:
                        parsed_unit = eval(unit_expr)  # noqa: S307
                        return value * parsed_unit
                    except Exception:
                        # If unit parsing fails, return as dimensionless
                        return Unum.coerceToUnum(value)
                return Unum.coerceToUnum(value)
            return Unum.coerceToUnum(value)

        if type_tag == "ndarray":
            value = deserialize(obj[_VALUE_TAG], reconstruct_classes)
            dtype_str = obj.get(_DTYPE_TAG)
            if dtype_str:
                try:
                    dtype = np.dtype(dtype_str)
                except TypeError:
                    # Structured dtype - need to eval the string representation
                    dtype = np.dtype(eval(dtype_str))  # noqa: S307
                return np.array(value, dtype=dtype)
            return np.array(value)

        if type_tag == "np_scalar":
            value = obj[_VALUE_TAG]
            dtype_str = obj.get(_DTYPE_TAG, "float64")
            return np.dtype(dtype_str).type(value)

        if type_tag == "dtype":
            return np.dtype(obj[_VALUE_TAG])

        if type_tag == "set":
            return set(deserialize(item, reconstruct_classes) for item in obj[_VALUE_TAG])

        if type_tag == "frozenset":
            return frozenset(deserialize(item, reconstruct_classes) for item in obj[_VALUE_TAG])

        if type_tag == "tuple":
            return tuple(deserialize(item, reconstruct_classes) for item in obj[_VALUE_TAG])

        if type_tag == "object":
            # Deserialize the nested values first
            deserialized_attrs = {
                k: deserialize(v, reconstruct_classes)
                for k, v in obj[_VALUE_TAG].items()
            }

            if reconstruct_classes:
                # Reconstruct the original class instance
                class_path = obj.get("__class__")
                if class_path:
                    try:
                        cls = _import_class(class_path)
                        # Create instance without calling __init__
                        instance = object.__new__(cls)
                        instance.__dict__.update(deserialized_attrs)
                        return instance
                    except (ImportError, AttributeError) as e:
                        # Fall back to dict if class can't be imported
                        # Only warn once per class type to avoid spam
                        if class_path not in _warned_classes:
                            _warned_classes.add(class_path)
                            print(f"Warning: Could not reconstruct {class_path}: {e}")
                        return deserialized_attrs

            return deserialized_attrs

        if type_tag == "str_fallback":
            # Can't recover original type, return the string
            return obj[_VALUE_TAG]

        # Regular dict without type tag
        return {k: deserialize(v, reconstruct_classes) for k, v in obj.items()}

    if isinstance(obj, list):
        return [deserialize(item, reconstruct_classes) for item in obj]

    return obj


def serialize_simdata(obj: Any) -> bytes:
    return orjson.dumps(serialize(obj))


def _collect_types(obj: Any, types: dict[str, int], path: str = "", depth: int = 0, max_depth: int = 10) -> None:
    """Recursively collect type counts from a nested structure."""
    if depth > max_depth:
        return

    type_name = type(obj).__name__
    types[type_name] = types.get(type_name, 0) + 1

    if isinstance(obj, dict):
        for k, v in obj.items():
            _collect_types(v, types, f"{path}.{k}", depth + 1, max_depth)
    elif isinstance(obj, (list, tuple)):
        for i, item in enumerate(obj[:100]):  # Limit to first 100 items
            _collect_types(item, types, f"{path}[{i}]", depth + 1, max_depth)


def _compare_values(original: Any, restored: Any, path: str = "") -> list[str]:
    """Compare original and restored values, returning list of differences."""
    errors = []

    # Type comparison (allowing dict for objects that were class instances)
    rest_type = type(restored).__name__

    # Objects with __dict__ become dicts after round-trip
    if hasattr(original, "__dict__") and not isinstance(original, (dict, list, tuple, set, frozenset, np.ndarray, Unum)):
        if not isinstance(restored, dict):
            errors.append(f"{path}: expected dict for object, got {rest_type}")
            return errors
        # Compare the __dict__ contents
        return _compare_values(original.__dict__, restored, path)

    if isinstance(original, Unum):
        if not isinstance(restored, Unum):
            errors.append(f"{path}: expected Unum, got {rest_type}")
            return errors
        orig_val = original.asNumber()
        rest_val = restored.asNumber()
        if isinstance(orig_val, np.ndarray):
            if not np.allclose(orig_val, rest_val, rtol=1e-10, equal_nan=True):
                errors.append(f"{path}: Unum array values differ")
        elif not np.isclose(orig_val, rest_val, rtol=1e-10, equal_nan=True):
            errors.append(f"{path}: Unum values differ: {orig_val} vs {rest_val}")
        orig_unit = str(original.strUnit())
        rest_unit = str(restored.strUnit())
        if orig_unit != rest_unit:
            errors.append(f"{path}: Unum units differ: {orig_unit} vs {rest_unit}")
        return errors

    if isinstance(original, np.ndarray):
        if not isinstance(restored, np.ndarray):
            errors.append(f"{path}: expected ndarray, got {rest_type}")
            return errors
        if original.shape != restored.shape:
            errors.append(f"{path}: ndarray shapes differ: {original.shape} vs {restored.shape}")
            return errors
        if original.dtype != restored.dtype:
            errors.append(f"{path}: ndarray dtypes differ: {original.dtype} vs {restored.dtype}")
        # For numeric arrays, check values
        if np.issubdtype(original.dtype, np.number):
            if not np.allclose(original, restored, rtol=1e-10, equal_nan=True):
                errors.append(f"{path}: ndarray values differ")
        elif original.dtype.kind in ('U', 'S', 'O'):  # String or object arrays
            if not np.array_equal(original, restored):
                errors.append(f"{path}: ndarray values differ")
        return errors

    if isinstance(original, np.generic):
        if not isinstance(restored, np.generic):
            errors.append(f"{path}: expected np scalar, got {rest_type}")
            return errors
        if original.dtype != restored.dtype:
            errors.append(f"{path}: np scalar dtypes differ")
        if original != restored:
            errors.append(f"{path}: np scalar values differ")
        return errors

    if isinstance(original, tuple):
        if not isinstance(restored, tuple):
            errors.append(f"{path}: expected tuple, got {rest_type}")
            return errors
        if len(original) != len(restored):
            errors.append(f"{path}: tuple lengths differ: {len(original)} vs {len(restored)}")
            return errors
        for i, (o, r) in enumerate(zip(original, restored)):
            errors.extend(_compare_values(o, r, f"{path}[{i}]"))
        return errors

    if isinstance(original, (set, frozenset)):
        expected_type = set if isinstance(original, set) else frozenset
        if not isinstance(restored, expected_type):
            errors.append(f"{path}: expected {expected_type.__name__}, got {rest_type}")
            return errors
        if original != restored:
            errors.append(f"{path}: {expected_type.__name__} values differ")
        return errors

    if isinstance(original, dict):
        if not isinstance(restored, dict):
            errors.append(f"{path}: expected dict, got {rest_type}")
            return errors
        orig_keys = set(str(k) for k in original.keys())
        rest_keys = set(restored.keys())
        if orig_keys != rest_keys:
            missing = orig_keys - rest_keys
            extra = rest_keys - orig_keys
            if missing:
                errors.append(f"{path}: missing keys: {list(missing)[:5]}")
            if extra:
                errors.append(f"{path}: extra keys: {list(extra)[:5]}")
            return errors
        for k, v in original.items():
            errors.extend(_compare_values(v, restored[str(k)], f"{path}.{k}"))
        return errors

    if isinstance(original, list):
        if not isinstance(restored, list):
            errors.append(f"{path}: expected list, got {rest_type}")
            return errors
        if len(original) != len(restored):
            errors.append(f"{path}: list lengths differ: {len(original)} vs {len(restored)}")
            return errors
        for i, (o, r) in enumerate(zip(original, restored)):
            errors.extend(_compare_values(o, r, f"{path}[{i}]"))
        return errors

    # Primitive types
    if original != restored:
        errors.append(f"{path}: values differ: {original!r} vs {restored!r}")

    return errors


def test_simdata(verbose: bool = True) -> None:
    """Test serialization and deserialization of SimulationDataEcoli.

    Args:
        verbose: If True, print progress and results.

    Returns:
        A dict containing test results with keys:
        - 'success': bool indicating if all tests passed
        - 'serialization_ok': bool
        - 'deserialization_ok': bool
        - 'round_trip_ok': bool
        - 'file_size_bytes': int
        - 'original_types': dict of type counts in original data
        - 'restored_types': dict of type counts in restored data
        - 'errors': list of error messages
    """
    results: dict[str, Any] = {
        "success": False,
        "serialization_ok": False,
        "deserialization_ok": False,
        "round_trip_ok": False,
        "file_size_bytes": 0,
        "original_types": {},
        "restored_types": {},
        "errors": [],
    }

    simdata_path = Path("reconstruction/sim_data/kb/simData.cPickle")

    # Step 1: Load original simdata
    if verbose:
        print("Loading original simData...")
    try:
        theta = SimulationParameterDataset(simdata_path=simdata_path)
        original_simdata = theta.sim_data
    except Exception as e:
        results["errors"].append(f"Failed to load simdata: {e}")
        raise RuntimeError(f"Failed to load simdata: {e}")

    # Collect original types
    if verbose:
        print("Analyzing original data types...")
    _collect_types(original_simdata.__dict__, results["original_types"])

    # Step 2: Serialize
    if verbose:
        print("Serializing...")
    try:
        serialized = theta.export()
        results["serialization_ok"] = True
        results["file_size_bytes"] = len(serialized)
        if verbose:
            print(f"  Serialized size: {len(serialized) / 1024 / 1024:.2f} MB")
    except Exception as e:
        results["errors"].append(f"Serialization failed: {e}")
        raise RuntimeError(f"Serialization failed: {e}")

    # Step 3: Deserialize
    if verbose:
        print("Deserializing...")
    try:
        restored = SimulationParameterDataset.load()
        restored_simdata = restored.sim_data
        results["deserialization_ok"] = True
    except Exception as e:
        results["errors"].append(f"Deserialization failed: {e}")
        raise RuntimeError(f"Deserialization failed: {e}")

    # Collect restored types
    if verbose:
        print("Analyzing restored data types...")
    _collect_types(restored_simdata, results["restored_types"])

    # Step 4: Verify round-trip
    if verbose:
        print("Verifying round-trip integrity...")

    # Compare type counts
    orig_types = results["original_types"]
    rest_types = results["restored_types"]

    type_mismatches = []
    for type_name in set(orig_types.keys()) | set(rest_types.keys()):
        orig_count = orig_types.get(type_name, 0)
        rest_count = rest_types.get(type_name, 0)
        # Objects become dicts, so we expect dict count to increase
        if type_name not in ("dict",) and orig_count != rest_count:
            type_mismatches.append(f"  {type_name}: {orig_count} -> {rest_count}")

    if type_mismatches and verbose:
        print("Type count changes (expected for class->dict conversion):")
        for mismatch in type_mismatches[:10]:
            print(mismatch)

    # Spot-check specific values
    if verbose:
        print("Spot-checking values...")

    spot_check_errors = []

    # Check a Unum value
    if hasattr(original_simdata, "doubling_time"):
        errs = _compare_values(
            original_simdata.doubling_time,
            restored_simdata.get("doubling_time") if isinstance(restored_simdata, dict) else restored_simdata.doubling_time,
            "doubling_time"
        )
        spot_check_errors.extend(errs)

    # Check some nested structures by sampling a few top-level attributes
    if hasattr(original_simdata, "__dict__"):
        for attr_name in list(original_simdata.__dict__.keys())[:20]:
            orig_val = getattr(original_simdata, attr_name)
            rest_val = restored_simdata.get(attr_name) if isinstance(restored_simdata, dict) else getattr(restored_simdata, attr_name, None)
            if rest_val is None:
                spot_check_errors.append(f"{attr_name}: missing in restored data")
                continue
            errs = _compare_values(orig_val, rest_val, attr_name)
            spot_check_errors.extend(errs)

    results["errors"].extend(spot_check_errors)

    # Determine success
    results["round_trip_ok"] = len(spot_check_errors) == 0
    results["success"] = (
        results["serialization_ok"]
        and results["deserialization_ok"]
        and results["round_trip_ok"]
    )

    # Print summary
    serialization_result = 'PASS' if results['serialization_ok'] else 'FAIL'
    deserialization_result = 'PASS' if results['deserialization_ok'] else 'FAIL'
    roundtrip_result = 'PASS' if results['round_trip_ok'] else 'FAIL'
    overall_result = 'PASS' if results['success'] else 'FAIL'
    original_n_types = len(orig_types)
    hydrated_n_types = len(rest_types)

    if verbose:
        print()
        print("=" * 50)
        print("TEST RESULTS")
        print("=" * 50)
        print(f"Serialization:   {serialization_result}")
        print(f"Deserialization: {deserialization_result}")
        print(f"Round-trip:      {roundtrip_result}")
        print(f"File size:       {results['file_size_bytes'] / 1024 / 1024:.2f} MB")
        print()
        print(f"Original types:  {original_n_types} distinct types")
        print(f"Restored types:  {hydrated_n_types} distinct types")

        if results["errors"]:
            print()
            print(f"Errors ({len(results['errors'])}):")
            for err in results["errors"][:20]:
                print(f"  - {err}")
            if len(results["errors"]) > 20:
                print(f"  ... and {len(results['errors']) - 20} more")

        print()
        print(f"OVERALL: {overall_result}")
        print("=" * 50)

    for result, res_name in [
        (serialization_result, 'serialization'),
        (deserialization_result, 'deserialization'),
        (roundtrip_result, 'roundtrip'),
        (overall_result, 'overall')
    ]:
        assert result == "PASS", f"{res_name} did not PASS; instead it is: {result}"

    assert original_n_types == hydrated_n_types


def test_simdata_serialization() -> None:
    from pathlib import Path
    # Load from pickle and export with proper tags
    theta = SimulationParameterDataset(Path("reconstruction/sim_data/kb/simData.cPickle"))
    theta.export(Path("simdata.json"))
    # Now you can load it
    theta2 = SimulationParameterDataset.load(Path("simdata.json"))
    type(theta2.sim_data)  # SimulationDataEcoli
    print()