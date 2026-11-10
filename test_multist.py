#!/usr/bin/env python3
"""Check the transaction-set breakdown for files holding several ST sets."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))

from sanitize834 import detect_layout, split_segments, build_report  # noqa: E402
from make_fixtures import isa  # noqa: E402

OUT = Path(__file__).parent / "samples"


def build(case: str) -> bytes:
    header = ["GS*BE*S*R*20261109*1200*1*X*005010X220A1", "ST*834*0001",
              "BGN*00*REF1*20261109*1200*00*X*2"]

    def sub(emp: str, mid: str, ins08: str = "AE") -> list[str]:
        return [
            f"INS*Y*18*021*30*A***{ins08}***20260101",
            f"REF*17*{emp}",
            f"NM1*IL*1*Last*First****34*{mid}",
            "HD*021**HLT*PLAN1*EMP",
        ]

    if case == "disjoint":
        # two transaction sets covering different people
        set1 = ["ST*834*0001", *sub("E1", "000000001"), *sub("E2", "000000002"),
                "SE*9*0001"]
        set2 = ["ST*834*0002", *sub("E3", "000000003"), "SE*5*0002"]
        segments = [isa("*", ":", "~", "000000001"), header[0], set1[0], set1[1], *set1[2:-1],
                    set1[-1], set2[0], *set2[1:-1], set2[-1],
                    "GE*2*1", "IEA*1*000000001"]

    elif case == "overlap":
        # the same people appear in both transaction sets
        set1 = ["ST*834*0001", *sub("E1", "000000001"), "SE*6*0001"]
        set2 = ["ST*834*0002", *sub("E1", "000000001"), "SE*6*0002"]
        segments = [isa("*", ":", "~", "000000001"), header[0], *set1, *set2,
                    "GE*2*1", "IEA*1*000000001"]

    elif case == "same_ins08":
        # different people, same INS08 (employment/relationship code) -> not overlap
        set1 = ["ST*834*0001", *sub("E1", "000000001", "AE"), "SE*6*0001"]
        set2 = ["ST*834*0002", *sub("E2", "000000002", "AE"), "SE*6*0002"]
        segments = [isa("*", ":", "~", "000000001"), header[0], *set1, *set2,
                    "GE*2*1", "IEA*1*000000001"]

    else:
        raise ValueError(case)

    return ("~\n".join(segments) + "~\n").encode("latin-1")


for case in ("disjoint", "overlap", "same_ins08"):
    path = OUT / f"multi_{case}.834"
    path.write_bytes(build(case))
    data = path.read_bytes()
    layout = detect_layout(data)
    segments, _, _ = split_segments(data[layout.body_start:], layout)
    print(f"--- {case} ---")
    print(build_report(segments, layout, path, len(data)))
    print()
