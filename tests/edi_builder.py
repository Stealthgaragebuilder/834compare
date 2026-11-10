"""Helpers to build tiny, valid 834 files for tests (no real data)."""

from __future__ import annotations


def isa_segment(element: str = "*", component: str = ":",
                terminator: str = "~", control: str = "000000001") -> str:
    """Build a byte-exact 106-character ISA segment."""
    sender = "SENDER".ljust(15)
    receiver = "RECEIVER".ljust(15)
    fields = [
        "ISA", "00", " " * 10, "00", " " * 10,
        "ZZ", sender, "ZZ", receiver,
        "261109", "1200", "^", "00501", control, "0", "P", component,
    ]
    segment = element.join(fields)
    assert len(segment) + len(terminator) == 106, len(segment) + len(terminator)
    return segment + terminator


def build_file(segments: list[str],
               element: str = "*", component: str = ":",
               terminator: str = "~", newline: str = "\n",
               control: str = "000000001", gs_control: str = "1",
               st_control: str = "0001") -> bytes:
    """Wrap ``segments`` (the ST body, excluding ISA/GS/ST/SE/GE/IEA) in a full
    ISA/GS/ST ... SE/GE/IEA envelope and return the file bytes.

    ``segments`` are authored with '*' and '~' and translated to the target
    element/terminator delimiters.
    """
    def convert(seg: str) -> str:
        if element != "*":
            seg = seg.replace("*", element)
        if terminator != "~":
            seg = seg.replace("~", terminator)
        return seg

    body = [convert(s) for s in segments]
    gs = element.join(["GS", "BE", "S", "R", "20261109", "1200",
                       gs_control, "X", "005010X220A1"])
    st = element.join(["ST", "834", st_control])
    se = element.join(["SE", str(len(body)), st_control])
    ge = element.join(["GE", "1", gs_control])
    iea = element.join(["IEA", "1", control])

    all_segments = [isa_segment(element, component, terminator, control),
                    gs, st, *body, se, ge, iea]
    text = (terminator + newline).join(all_segments) + terminator + newline
    return text.encode("latin-1")


def subscriber(emp: str | None, last: str, first: str, member_id: str,
               ref_first: bool = True) -> list[str]:
    """One subscriber group (INS*Y + identifying segments)."""
    ident = [f"REF*17*{emp}"] if emp else []
    nm1 = f"NM1*IL*1*{last}*{first}****34*{member_id}"
    body = ident + [nm1] if ref_first else [nm1] + ident
    return [
        "INS*Y*18*021*30*A***AE***20260101",
        *body,
        "PER*IP**TE*5555550100*EM*jdoe@example.com",
        "N3*123 MAIN ST*APT 4",
        "N4*SPRINGFIELD*IL*62704",
    ]


def dependent(rel: str, last: str, first: str, member_id: str | None,
              dob: str) -> list[str]:
    """One dependent block (INS*N + NM1 + DMG)."""
    nm1 = (f"NM1*IL*1*{last}*{first}****34*{member_id}" if member_id
           else f"NM1*IL*1*{last}*{first}")
    return [
        f"INS*N*{rel}*30*A***AE***20260101",
        nm1,
        f"DMG*D8*{dob}*F",
    ]


def coverage(plan: str, eff: str = "20260101", term: str | None = None) -> list[str]:
    segs = [f"HD*021**HLT*{plan}*EMP", f"DTP*348*D8*{eff}"]
    if term:
        segs.append(f"DTP*349*D8*{term}")
    segs.append("REF*1L*GROUP001")
    return segs
