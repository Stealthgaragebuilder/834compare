#!/usr/bin/env python3
"""
make_fixtures.py - build synthetic INT5151-style 834 files for testing.

These are fake files, not real data. They exist so we can prove the sanitizer and the
compare tool work before touching anything with real PII.

Files produced in ./samples:
  sample_a.834     baseline
  sample_b.834     comparison: plan change, new dependent, new/removed subscriber,
                   segment reorder, control-number-only envelope changes
  sample_delims.834  same content as A but pipe delimiters, CRLF, no ISA terminator
  tiny_bad.844     no ISA, to exercise error handling
"""

from __future__ import annotations

from pathlib import Path

SAMPLES = Path(__file__).parent / "samples"


def isa(element: str, component: str, terminator: str, control: str,
        date: str = "261109", time: str = "1200",
        sender: str = "SENDERINT5151", receiver: str = "RECEIVERWORKDAY") -> str:
    """Build a byte-exact 106-character ISA segment."""
    fields = [
        "ISA", "00", " " * 10, "00", " " * 10,
        "ZZ", sender.ljust(15), "ZZ", receiver.ljust(15),
        date, time, "^", "00501", control, "0", "P", component,
    ]
    segment = element.join(fields)
    assert len(segment) + len(terminator) == 106, len(segment) + len(terminator)
    return segment + terminator


def subscriber(emp: str | None, last: str, first: str, member_id: str,
               ins_date: str = "20260101", ref_first: bool = True) -> list[str]:
    """One subscriber group: INS*Y plus the identifying segments."""
    ident = [f"REF*17*{emp}"] if emp else []
    nm1 = f"NM1*IL*1*{last}*{first}****34*{member_id}"
    body = ident + [nm1] if ref_first else [nm1] + ident
    return [
        f"INS*Y*18*021*30*A***AE***{ins_date}",
        *body,
        "PER*IP**TE*5555550100*EM*jdoe@example.com",
        "N3*123 MAIN ST*APT 4",
        "N4*SPRINGFIELD*IL*62704",
    ]


def dependent(rel: str, last: str, first: str, member_id: str | None,
              dob: str, ref17: str | None = None) -> list[str]:
    """One dependent member loop (INS*N)."""
    ident = [f"REF*17*{ref17}"] if ref17 else []
    nm1 = (f"NM1*IL*1*{last}*{first}****34*{member_id}" if member_id
           else f"NM1*IL*1*{last}*{first}")
    return [
        f"INS*N*{rel}*30*A***AE***20260101",
        *ident,
        nm1,
        f"DMG*D8*{dob}*F",
    ]


def coverage(plan: str, line: str = "HLT", eff: str = "20260101",
             term: str | None = None) -> list[str]:
    """Loop 2300 HD plus its DTP/REF children."""
    segs = [f"HD*021**{line}*{plan}*EMP", f"DTP*348*D8*{eff}"]
    if term:
        segs.append(f"DTP*349*D8*{term}")
    segs.append("REF*1L*GROUP001")
    return segs


def write(path: Path, segments: list[str], element: str = "*", component: str = ":",
          terminator: str = "~", newline: str = "\n",
          control: str = "000000001", gs_control: str = "1",
          bgn: str = "REF0001", bgn_date: str = "20261109",
          st_control: str = "0001") -> None:
    # Segments are authored with '*' and ':' regardless of the target delimiters;
    # translate them here so every fixture is internally consistent.
    def convert(segment: str) -> str:
        return segment.replace(":", component).replace("*", element) if element != "*" else segment

    segments = [convert(seg) for seg in segments]
    isa_seg = isa(element, component, terminator, control)
    gs = element.join(["GS", "BE", "SENDERINT5151", "RECEIVERWORKDAY",
                       bgn_date, "1200", gs_control, "X", "005010X220A1"])
    st = element.join(["ST", "834", st_control])
    bgn_seg = element.join(["BGN", "00", bgn, bgn_date, "1200", "00", "X", "2"])
    ref_hdr = element.join(["REF", "38", "MASTERPOLICY99"])
    dtp_hdr = element.join(["DTP", "007", "D8", bgn_date])
    n1_payer = element.join(["N1", "P5", "ACME BENEFITS", "FI", "123456789"])
    n1_sponsor = element.join(["N1", "IN", "ACME CORP", "FI", "987654321"])

    body_segments = [gs, st, bgn_seg, ref_hdr, dtp_hdr, n1_payer, n1_sponsor, *segments]

    total = len(body_segments) + 3  # minus ISA/GS, plus SE/GE/IEA
    se = element.join(["SE", str(total), st_control])
    ge = element.join(["GE", "1", gs_control])
    iea = element.join(["IEA", "1", control])

    all_segments = [isa_seg, *body_segments, se, ge, iea]
    text = (terminator + newline).join(all_segments) + terminator + newline
    if newline:
        text = isa_seg + newline + (terminator + newline).join(body_segments + [se, ge, iea]) \
            + terminator + newline
    path.write_bytes(text.encode("latin-1"))
    print(f"wrote {path}  ({len(all_segments)} segments, {len(text):,} chars)")


def main() -> None:
    SAMPLES.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- File A
    a: list[str] = []
    # 1. subscriber with employee number; plan will change in B
    a += subscriber("EMP00123", "Smith", "John", "042640680")
    a += dependent("01", "Smith", "Erin", "042640681", "19850312")
    a += coverage("PLAN1")
    # 2. subscriber who gains a dependent in B
    a += subscriber("EMP00124", "Lopez", "Maria", "042640682")
    a += dependent("19", "Lopez", "Diego", "042640683", "20150304")
    a += coverage("PLAN1")
    # 3. subscriber only in A -> Removed
    a += subscriber("EMP00125", "Chen", "Wei", "042640684")
    a += coverage("PLAN2")
    # 4. subscriber with NO REF*17 -> must fall back to NM1*IL NM109
    a += subscriber(None, "Okafor", "Ada", "042640685")
    a += coverage("PLAN1")
    # 5. dependent with no ID at all -> composite key path
    a += subscriber("EMP00127", "Vargas", "Luis", "042640686")
    a += dependent("19", "Vargas", "Sofia", None, "20200304")
    a += coverage("PLAN1")
    write(SAMPLES / "sample_a.834", a, control="000000001", gs_control="1",
          bgn="REF0001", bgn_date="20261109")

    # ---------------------------------------------------------------- File B
    b: list[str] = []
    # 1. plan changed PLAN1 -> PLAN2, and segments reordered (REF after NM1)
    b += subscriber("EMP00123", "Smith", "John", "042640680", ref_first=False)
    b += dependent("01", "Smith", "Erin", "042640681", "19850312")
    b += coverage("PLAN2", term="20261231")
    # 2. new dependent added under Lopez
    b += subscriber("EMP00124", "Lopez", "Maria", "042640682")
    b += dependent("19", "Lopez", "Diego", "042640683", "20150304")
    b += dependent("19", "Lopez", "Mateo", "042640687", "20250812")
    b += coverage("PLAN1")
    # 3. subscriber only in B -> New (Patel)
    b += subscriber("EMP00126", "Patel", "Aisha", "042640688")
    b += coverage("PLAN1")
    # 4. same as A, still no REF*17 -> Unchanged
    b += subscriber(None, "Okafor", "Ada", "042640685")
    b += coverage("PLAN1")
    # 5. same as A -> Unchanged
    b += subscriber("EMP00127", "Vargas", "Luis", "042640686")
    b += dependent("19", "Vargas", "Sofia", None, "20200304")
    b += coverage("PLAN1")
    # envelope-only differences: control numbers, BGN id/date, ST control
    write(SAMPLES / "sample_b.834", b, control="000000002", gs_control="9",
          bgn="REF0002", bgn_date="20261110", st_control="0007")

    # ------------------------------------------- same content, other delimiters
    write(SAMPLES / "sample_delims.834", a, element="|", component=":",
          terminator="~", newline="\r\n", control="000000003", gs_control="3",
          bgn="REF0003", bgn_date="20261109")

    # -------------------------------------------------- deliberately broken file
    (SAMPLES / "tiny_bad.834").write_bytes(b"GS*BE*X*Y*20261109*1200*1*X*005010X220A1~\n")
    print(f"wrote {SAMPLES / 'tiny_bad.834'}  (no ISA - should be rejected)")

    # --------------------------------------------- line-break only, no terminator
    isa_only = isa("*", ":", "\n", "000000004")
    text = isa_only + "\n" + "~\n".join([
        "GS*BE*S*R*20261109*1200*4*X*005010X220A1",
        "ST*834*0004",
        "BGN*00*REF0004*20261109*1200*00*X*2",
        "INS*Y*18*021*30*A***AE***20260101",
        "REF*17*EMP00123",
        "NM1*IL*1*Smith*John****34*042640680",
        "HD*021**HLT*PLAN1*EMP",
        "DTP*348*D8*20260101",
        "SE*9*0004", "GE*1*4", "IEA*1*000000004",
    ]) + "~\n"
    (SAMPLES / "sample_newline_only.834").write_bytes(text.encode("latin-1"))
    print(f"wrote {SAMPLES / 'sample_newline_only.834'}  (newline separators only)")


if __name__ == "__main__":
    main()
