# Technical invariants

The translator may rewrite prose, but it must not silently rewrite machine- or
document-significant tokens. `pdf2zh/invariants.py` compares source and output
multisets and rejects a segment when supported invariants are removed, changed,
duplicated, or invented.

Protected categories are structural tags and placeholders, URLs and common file
paths, IPv4 addresses and protocol ports, PLC/robot variables, model/connector/
alarm codes, standards and document identifiers, versions, numeric literals,
engineering ranges and units, and literal `ON`/`OFF` states. Detection is
deliberately conservative; it is a safety net, not a complete vendor grammar.
The plant acronym `PCW` and indexed disposition positions such as `OK#1` and
`NG#1` are treated as identifiers; ordinary uppercase prose is not.
Executable PLC/robot instruction tokens are also immutable. Value/unit spacing
is normalized for comparison, so `2350mm` and `2350 mm` are equivalent while a
changed value, sign, or unit is rejected.

Verified proper names come from a small reviewed registry. Matching is exact,
case-sensitive, boundary-safe, and leftmost-longest. A standalone reviewed name
is preserved locally. In mixed prose, only matching spans are hidden behind
fresh safe tags, restored byte-for-byte, and checked for equal source/target
occurrence counts before cache acceptance. A confirmed document terminology
entry for that exact alias overrides default preservation.

Explicit adjacent assignments to recognized identifiers, numbered parameters,
and named axes are compared by object/value association when both source and
target use supported forms (including reordered clauses). This is not a full
vendor grammar. English opcode words are protected as code only in supported
instruction syntax; a prose sentence starting with `SET` still needs translation.

Vietnamese-target validation also flags loss of recognized prohibition,
requirement, recommendation, and permission cues in English/Korean/Chinese.
Before/after direction is compared only when both actions have distinct stable
technical anchors. These lightweight guards feed the existing targeted retry;
they do not prove all negation scope, paraphrases, or action semantics.

Korean-only checks additionally preserve supported `필요`/`필수`,
`권장`/`권고`, `가능`/`불가`, `경우`, and anchored `전`/`후` distinctions.
Known technical English and Korean-marked UI labels are exact unless the
confirmed terminology map explicitly supplies a different rendering.

## Conflict resolution

Apply one order only:

1. Preserve structural placeholders and tags.
2. Preserve immutable technical identifiers, document/standard IDs, and literal code tokens.
3. Preserve numbers and engineering units.
4. Preserve semantic polarity: negation, prohibition, `ON`/`OFF`, enable/disable.
5. Preserve requirement strength and temporal/conditional relationships.
6. Apply confirmed document-specific terminology.
7. Preserve reviewed proper names without a document-specific override.
8. Apply confirmed vendor/domain terminology.
9. Produce natural Vietnamese.

Natural phrasing never overrides an earlier item. A validator rejection leaves
the source segment in place and records it as unresolved; it must not be called
translated. Do not broaden regular expressions merely to lock every uppercase
word: false positives can make otherwise translatable prose permanently remain
in the source language.

Controlled terms such as interlock, homing, payload, singularity, teach point,
Servo ON, JOG, Fine, or NWAIT are not automatically immutable. Preserve or
translate them according to confirmed document/vendor terminology and context.
