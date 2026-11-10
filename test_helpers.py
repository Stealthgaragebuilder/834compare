#!/usr/bin/env python3
"""Quick checks for the sanitizer's replacement helpers."""

from sanitize834 import fake_name, scramble, fake_date, fake_email

failures = 0


def check(label: str, condition: bool) -> None:
    global failures
    if condition:
        print(f"PASS  {label}")
    else:
        failures += 1
        print(f"FAIL  {label}")


# --- names must never map to themselves, even when the pool contains the original ---
pool = ("Okafor", "Vargas", "Patel")
for real in pool:
    fake = fake_name(real, "salt", pool)
    check(f"name {real} -> {fake} (differs)", fake.lower() != real.lower())

check("name Smith -> fake (differs)",
      fake_name("Smith", "salt", ("Okafor",)).lower() != "smith")
check("name casing preserved (upper)",
      fake_name("OKAFOR", "salt", pool).isupper())
check("name empty stays empty", fake_name("", "salt", pool) == "")

# --- deterministic: same input, same output ---
check("names deterministic", fake_name("Smith", "s1", pool) == fake_name("Smith", "s1", pool))
check("IDs deterministic", scramble("042640680", "s1") == scramble("042640680", "s1"))
check("different salt gives different ID",
      scramble("042640680", "s1") != scramble("042640680", "s2"))

# --- scramble behaviour ---
check("leading zero preserved", scramble("042640680", "x").startswith("0"))
check("length preserved", len(scramble("042640680", "x")) == 9)
check("digits stay digits", scramble("12345", "x").isdigit())
check("all-zeros does not pass through", scramble("0000", "x") != "0000")
check("empty stays empty", scramble("", "x") == "")
check("non-alphanumerics preserved", "-" in scramble("12-34", "x"))

# --- dates ---
check("date format 8 kept", len(fake_date("19850312", "x")) == 8)
check("date format 6 kept", len(fake_date("850312", "x")) == 6)
check("date changed", fake_date("19850312", "x") != "19850312")
check("non-date untouched", fake_date("ABCDEF", "x") == "ABCDEF")

# --- e-mail ---
mail = fake_email("someone@realcorp.com", "x")
check("e-mail uses example.com", mail.endswith("@example.com"))
check("e-mail changed", mail != "someone@realcorp.com")

print()
print("ALL HELPER CHECKS PASSED" if failures == 0 else f"{failures} HELPER CHECK(S) FAILED")
raise SystemExit(0 if failures == 0 else 1)
