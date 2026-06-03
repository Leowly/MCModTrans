"""Cross-mod reference analyzer — detect missing translation keys."""
from __future__ import annotations

import logging
import re
from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass
logger = logging.getLogger(__name__)

# Key format with colon: item.<modid>:<subtype>.<other_modid>:<effect>
# Group 1 = prefix (up to last dot before ref_modid)
# Group 2 = ref_modid
# Group 3 = effect_name
CROSS_MOD_KEY_COLON = re.compile(
    r"^((?:item|tile|block|entity|tooltip)\.[\w-]*:[\w.]*\.)"
    r"(\w[\w-]*):(\w[\w.-]+)$"
)

# Dot-separated: item.<modid>:<subtype>.<other_modid>.<effect>
CROSS_MOD_KEY_DOT = re.compile(
    r"^((?:item|tile|block|entity|tooltip)\.[\w-]*:[\w.]*\.)"
    r"(\w[\w-]*)\.(\w[\w.-]+)$"
)

_EFFECT_PATTERNS = [
    re.compile(r"^mob_effect\.(\w[\w-]*):([\w-]+)$"),
    re.compile(r"^potion(?:\.long|\.strong)?\.effect\.(\w[\w-]*):([\w-]+)$"),
    re.compile(r"^tipped_arrow\.effect\.(\w[\w-]*):([\w-]+)$"),
    re.compile(r"^(?:lingering|splash)_potion\.effect\.(\w[\w-]*):([\w-]+)$"),
    re.compile(r"^bestiary\.effect\.([\w-]+)$"),
]
_STRIP_COLOR = re.compile(r"§.")

def analyze_cross_mod_gaps(mods):
    m_idx = {m.modid: m for m in mods}
    pats = _find_patterns(mods, m_idx)
    gaps = []
    for src_id, prefix, ref_id, existing, sep in pats:
        src = m_idx.get(src_id)
        ref = m_idx.get(ref_id)
        if not src or not ref:
            continue
        avail = _extract_effects(ref)
        if not avail:
            continue
        missing = avail - existing
        if not missing:
            continue
        logger.info("Cross-mod [%s -> %s]: missing %d/%d effects",
                     src_id, ref_id, len(missing), len(avail))
        for eff in sorted(missing):
            fk = prefix + ref_id + sep + eff
            sug = _infer(eff, src, prefix, ref, existing, ref_id)
            if sug:
                gaps.append({"source_modid": src_id, "key": fk, "suggested_en": sug,
                             "referenced_modid": ref_id, "effect_name": eff})
                logger.debug("  + %s = %s", fk, sug)
    return gaps

def apply_gaps(mods, gaps):
    m_map = {m.modid: m for m in mods}
    added = 0
    for g in gaps:
        src = m_map.get(g["source_modid"])
        if not src:
            continue
        k, v = g["key"], g.get("suggested_en", "")
        if k not in src.english_entries and v:
            src.english_entries[k] = v
            added += 1
    return added

def analyze_and_apply(mods):
    return apply_gaps(mods, analyze_cross_mod_gaps(mods))

def _find_patterns(mods, m_idx):
    refs = []
    for mod in mods:
        for key in mod.english_entries:
            m = CROSS_MOD_KEY_COLON.match(key)
            if m:
                prefix, ref_id, effect = m.group(1), m.group(2), m.group(3)
                sep = ":"
            else:
                m = CROSS_MOD_KEY_DOT.match(key)
                if m:
                    prefix, ref_id, effect = m.group(1), m.group(2), m.group(3)
                    sep = "."
                else:
                    continue
            if ref_id in m_idx:
                refs.append((mod.modid, prefix, ref_id, effect, sep))
    groups = {}
    for s, p, r, e, sp in refs:
        groups.setdefault((s, p, r, sp), set()).add(e)
    return [(s, p, r, ef, sp) for (s, p, r, sp), ef in groups.items() if len(ef) >= 2]

def _extract_effects(mod):
    modid = mod.modid
    effects = set()
    for key in mod.english_entries:
        for pat in _EFFECT_PATTERNS:
            m = pat.match(key)
            if m:
                if m.lastindex == 2 and m.group(1) == modid:
                    effects.add(m.group(2))
                elif m.lastindex == 1:
                    effects.add(m.group(1))
                break
    return effects

def _classify(mod, prefix, ref_id, existing, sep):
    vals = [mod.english_entries.get(prefix + ref_id + sep + e, "")
            for e in existing if "%s" in mod.english_entries.get(prefix + ref_id + sep + e, "")]
    if not vals:
        return "%s of X"
    c = Counter()
    for v in vals:
        c["%s of X" if "%s of " in v else "X %s"] += 1
    return c.most_common(1)[0][0]

def _display_name(eff, ref):
    modid = ref.modid
    for base in [f"mob_effect.{modid}", f"potion.effect.{modid}",
                 f"tipped_arrow.effect.{modid}", f"lingering_potion.effect.{modid}",
                 f"splash_potion.effect.{modid}"]:
        val = ref.english_entries.get(f"{base}:{eff}")
        if not val:
            continue
        clean = _STRIP_COLOR.sub("", val).strip()
        for pfx in ("Arrow of ", "Splash Potion of ", "Lingering Potion of ",
                     "Potion of ", "Tipped Arrow of "):
            if clean.startswith(pfx):
                clean = clean[len(pfx):].strip()
                break
        if clean:
            return clean
    return None

def _infer(eff, src, prefix, ref, existing, ref_id):
    # Determine separator from existing entries
    sep = ":"
    for e in existing:
        k = prefix + ref_id + ":" + e
        if k in src.english_entries:
            sep = ":"
            break
        k = prefix + ref_id + "." + e
        if k in src.english_entries:
            sep = "."
            break
    fmt = _classify(src, prefix, ref_id, existing, sep)
    name = _display_name(eff, ref) or eff.replace("_", " ").title()
    return f"%s of {name}" if fmt == "%s of X" else f"{name} %s"
