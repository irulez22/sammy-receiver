"""Turn OCR words from Spider Sammy's bubble band into code parts.

The bubbles stack downward while a code is revealed ("CODE IS" -> "LOVE" ->
"DONUTS"), so a code is assembled from one part per bubble. Everything else in
the band -- hint sentences ("this code has NO NUMBERS"), HUD counters ("28"),
world labels ("SpyderSammy's Base") -- must be dropped instead of concatenated.

This module owns that rule. `code_sim.py` scores readings with it; the VOD
harvest in `.blocks/collect_codes.py` still has its own copy and is the next
caller to migrate.
"""
import difflib
import re

PART_MAX = 32          # longest token that can be a code; codes reach 17 chars
CODE_MAX = 17          # longest code seen; a spaced bubble longer than this is prose
PART_MIN_DIGITS = 3    # a shorter all-digit token is a HUD counter, not a part
MERGE_SIMILARITY = 0.6  # readings this close are OCR corrections, not new parts

# The phrase that announces a code; the words around it are not code. `has` is
# deliberately absent: "this code has numbers" is a hint, not an announcement.
PHRASE = re.compile(r"use\s*c[o0]de|c[o0]de\s*(?:was|is|:)", re.IGNORECASE)

# Bubble text that is a riddle rule rather than a code part, whatever its case.
HINT_WORDS = {
    "this", "that", "these", "has", "have", "does", "do", "no", "not", "numbers",
    "number", "predictable", "riddle", "wait", "hint", "end", "beginning", "start",
    "letters", "letter", "digits", "digit", "ago", "along",
}

# Prose that surrounds a code on a mangled line; at most it hides the code, it
# never is one. Only applied to multi-word lines, so a lone "IS" bubble survives.
PROSE_STOP = {
    "the", "a", "an", "and", "but", "to", "it", "is", "was", "at", "in", "of",
    "on", "for", "with", "from", "you", "we", "they", "he", "she", "will",
}

# A lowercase token that opens with one of these is a fused hint fragment
# ("numbersat", "predictal"), never a code.
HINT_STEMS = ("this", "that", "these", "has", "have", "does", "not", "no",
              "numbers", "number", "predict", "riddle", "wait", "hint", "end",
              "start", "begin", "letter", "digit")

# Chat/prose vocabulary that appears next to a code but is never part of one.
STOPWORDS = {
    "call", "collect", "machine", "guaranteed", "legendary", "spawns", "spawned",
    "shop", "base", "codes", "code", "trade", "select", "brainrots", "offer",
    "school", "money", "your", "index", "rebirth", "duels", "robux", "cash",
    "gone", "live", "empty", "secret", "team", "spin", "wheel", "free",
    "ok", "okay", "yes", "lol", "wow", "nice", "hmm", "thanks",
}

# Chat interjections: stopwords in prose, but an all-caps one can be a part.
INTERJECTIONS = {"okay", "yes", "lol", "wow", "nice", "hmm", "thanks"}

# Words that belong to the announcement, dropped only from the line that
# carries it: a standalone "IS" bubble is a real part (CANDY|IS|YUMMY).
WORD_STOP = {"is", "was", "the", "and", "to", "it", "a", "use", "user"}

TOKEN = re.compile(r"[A-Za-z0-9]+")


def rows(words, tolerance=0.35, gap=4.0):
    """Group OCR words into visual rows, top to bottom.

    Grouping is by vertical centre first, then by horizontal gaps *after*
    sorting by x. Sorting by (top, left) instead put a word that sits a few
    pixels lower (`was`) after the next word, so the gap to that farther word
    looked huge and one chat line became two -- which cost the announcement
    phrase and the whole code with it.
    """
    ordered = sorted(words, key=lambda word: (word["top"], word["left"]))
    grouped = []
    for word in ordered:
        if grouped:
            row = grouped[-1]
            height = max(row[0]["height"], word["height"])
            middle = row[0]["top"] + row[0]["height"] / 2
            if abs((word["top"] + word["height"] / 2) - middle) <= tolerance * height:
                row.append(word)
                continue
        grouped.append([word])

    lines = []
    for row in grouped:
        row = sorted(row, key=lambda item: item["left"])
        line = [row[0]]
        for word in row[1:]:
            height = max(line[-1]["height"], word["height"])
            if word["left"] - (line[-1]["left"] + line[-1]["width"]) > gap * height:
                lines.append(line)
                line = [word]
            else:
                line.append(word)
        lines.append(line)
    return sorted(lines, key=lambda line: line[0]["top"])


def line_text(row):
    return " ".join(word["text"] for word in row if word["text"])


# A single glyph is a real part only when it is one of the shapes a code uses
# alone (`I` in ILOVEDONUTS, the digit in ...NINO15). "S" or "E" alone is noise.
SINGLE_GLYPHS = {"i", "l", "1", "7", "0", "o"}


def looks_like_part(token, allow_short_digits=False):
    """True when a lone token can be a code part rather than scenery."""
    if not 1 <= len(token) <= PART_MAX:
        return False
    if len(token) == 1 and token.lower() not in SINGLE_GLYPHS:
        return False
    if token.isdigit():
        minimum = 2 if allow_short_digits else PART_MIN_DIGITS
        return len(token) >= minimum
    lowered = token.lower()
    if lowered in HINT_WORDS or lowered in STOPWORDS:
        # Codes are CAPS: an all-caps interjection bubble is a part ("VEF|HMM|UFW",
        # rehearsal 2026-11-21, was dropped); chat in mixed case still is not.
        if not (lowered in INTERJECTIONS and token.isupper() and len(token) >= 3):
            return False
    # Codes are CAPS, so a lowercase letter is OCR noise, not a case change --
    # except when it is a whole prose word fused in ("atthelend912125559").
    # Four or more lowercase letters in a row only happens inside prose.
    if re.search(r"[a-z]{4,}", token):
        return False
    # OCR fuses a hint's words ("numbers at" -> "numbersat"), leaving a fragment
    # that looks like a lowercase code. A lowercase token that opens with hint
    # vocabulary is that fragment, not a code.
    if token.islower() and any(lowered.startswith(stem) for stem in HINT_STEMS):
        return False
    return True


def code_shaped(token):
    """Stricter test for picking a code out of a sentence.

    Capitalization is what separates a code from the words around it: admin
    codes are digits, named codes are uppercase. Lowercase event codes are real,
    but a lowercase word next to prose is far more often a hint or chat word
    ("psedictal" from "is not predictable"), and a dropped code is cheaper than
    a wrong one, so prose only yields uppercase or digit-bearing tokens.
    """
    if not looks_like_part(token) or len(token) < 4:
        return False
    return any(character.isdigit() for character in token) or token.isupper()


def inline_code(line):
    """The whole code when it is written on the announcement bubble itself
    ("use code BLINGBLINGBLING", real screenshot 2026-11-26), else "".

    A decoy like "USE CODE 0000" (live 2026-11-25, followed by the real code
    in separate bubbles) is not one: an inline code must be a single word of
    5+ letters/digits that is not one character repeated. Such a code is
    complete on its own; it never joins later bubbles.
    """
    text = line.strip()
    match = PHRASE.search(text)
    if not match:
        return ""
    rest = text[match.end():].strip(" .,:;!?'\"|…-")
    tokens = TOKEN.findall(rest)
    tokens = [t for t in tokens if t.lower() not in WORD_STOP]
    if len(tokens) != 1:
        return ""
    token = tokens[0]
    if not token.isalnum() or not 5 <= len(token) <= CODE_MAX or len(set(token.lower())) < 2:
        return ""
    return token.upper()


# Nothing on the "use code" / "the code was" bubble itself is ever a code part.
ANNOUNCEMENT_NEVER_A_PART = True


def parts_from_lines(lines, short_digits=False):
    """Split OCR lines into (code parts, hint lines), in reading order.

    A line is a part when it holds exactly one code-shaped token after the
    announcement phrase is removed. A line with several words is prose -- a
    riddle hint or a chat sentence -- unless exactly one of those words is
    code-shaped, which is the case when OCR drops the phrase around a code
    ("the HWKRAXCQWJ"). Anything left over is reported as a hint instead of
    being glued into the code.
    """
    parts, hints = [], []
    for line in lines:
        text = line.strip()
        if not text:
            continue
        has_phrase = bool(PHRASE.search(text))
        stripped = PHRASE.sub(" ", text) if has_phrase else text
        stripped = stripped.strip(" .,:;!?'\"|…")
        if not stripped:
            continue
        tokens = TOKEN.findall(stripped)
        if has_phrase and ANNOUNCEMENT_NEVER_A_PART:
            # The code never starts on the announcement's own bubble (user,
            # 2026-11-26): a decoy "USE CODE 0000" put 0000 in front of the
            # real code and submitted 0000TURKEYIURKEY (live 2026-11-25).
            continue
        if has_phrase:
            tokens = [token for token in tokens if token.lower() not in WORD_STOP]
            # Codes are CAPS: an all-lowercase word on the announcement line
            # is Sammy's filler ("use code... ooo", VOD 8cX 857s), not a part.
            tokens = [token for token in tokens if not (token.isalpha() and token.islower())]
            # A lone glyph after the phrase is scenery at the line's end ("the
            # code was I", real client 2026-11-21: 7 wrong codes started with I).
            # A code carried on the announcement line is never one glyph long.
            if len(tokens) == 1 and len(tokens[0]) == 1:
                tokens = []
        if not tokens:
            continue
        if (len(tokens) == 1 and len(tokens[0]) >= 2 and tokens[0].isupper()
                and tokens[0].lower() in HINT_WORDS):
            # A lone all-caps bubble is a part even when it is a hint word:
            # ILOVE|DO|NUTS lost "DO" (100-code round, 2026-11-21). Hints are
            # sentences, never one caps word.
            parts.append(tokens[0])
            continue
        token = tokens[0]
        lower_case = sum(c.islower() for c in token) if len(tokens) == 1 else 0
        if (not has_phrase and lower_case
                and lower_case >= sum(c.isupper() for c in token)
                and 2 <= len(token) <= CODE_MAX
                and not (len(token) >= 4 and len(set(token.lower())) <= 2)):
            # A one-word lowercase bubble after the announcement is a part,
            # however short or word-like (go|bblecan, do|ritosp, noob, pug:
            # rehearsal 2026-11-26 lost them and once assembled OBBLECANDYLAVA).
            # Everything after the announcement is the code (user, 2026-11-26);
            # hints are sentences and come before it, so hint words are not
            # filtered here.
            # A code never mixes case (user, 2026-11-26), so a capital in a
            # lowercase word is an OCR slip (ypuggY = ypuggy) and is just
            # lowercased. Filler like "eoooo" (two letters or fewer) is still
            # not a part.
            if "I" in token or "O" in token:
                # ...except where the capital could be a digit too: I is l or
                # 1 (1tiu read Itiu), O is o or 0 (0ij6o read Oij60). Unsure,
                # so no part: the app then refuses the code (missed, not wrong).
                hints.append(" ".join(text.split()))
                continue
            fixed = token.lower()
            parts.append(fixed.upper())
            continue
        if (len(tokens) == 1 and not has_phrase and token.isalpha() and token.islower()
                and 4 <= len(token) <= CODE_MAX and len(set(token)) >= 3
                and token not in HINT_WORDS and token not in STOPWORDS
                and not token.startswith(HINT_STEMS)):
            # A bubble that is one lowercase word is a part: Sammy writes codes
            # in lowercase too ("the code is|gobble|gob|747", live 2026-11-21,
            # lost "gobble"). Filler like "eoooo" (under 3 distinct letters),
            # hint words and fused hint fragments are still not parts.
            parts.append(token.upper())
            continue
        if len(tokens) == 1 and looks_like_part(tokens[0], short_digits):
            # Codes are CAPS: a lowercase glyph is an OCR slip, not a case.
            parts.append(tokens[0].upper())
            continue
        if (not has_phrase and len(tokens) >= 2
                and all(token.isupper() or token.isdigit() for token in tokens)
                and len("".join(tokens)) <= CODE_MAX
                and not any(token.lower() in HINT_WORDS or token.lower() in STOPWORDS
                            for token in tokens)):
            # Codes never contain spaces: an all-caps bubble the engine split
            # ("BESTBR" read as "BEST BR", fake client 2026-11-21) is one part.
            # Keeping only BEST, then re-reading BESTBR, duplicated a segment.
            parts.append("".join(tokens))
            continue
        prose = [token for token in tokens if token.lower() not in PROSE_STOP]
        shaped = [token for token in prose if code_shaped(token)]
        if len(shaped) == 1:
            parts.append(shaped[0].upper())
        elif short_digits and tokens[0].isdigit() and not any(
                token.lower() in HINT_WORDS or token.isalpha() for token in tokens):
            # A line of digits with no words is never a riddle hint -- hints are
            # sentences -- and the reader only sees the bubble band, where a
            # `38` bubble is a genuine part. Treating it as a hint dropped the
            # whole first segment of `382178`.
            parts.append(tokens[0].upper())
        else:
            hints.append(" ".join(text.split()))
    return parts, hints
    return parts, hints


def parts_from_words(words, min_height=0.0):
    """Same split, straight from `winocr.recognize_words` output."""
    kept = [word for word in words if word["height"] >= min_height]
    return parts_from_lines([line_text(row) for row in rows(kept)])


def assemble(parts):
    return "".join(parts)


# Riddle hints that constrain what a *finished* code looks like. The hint always
# arrives before the code, so it is the only evidence available while the parts
# are still landing: a code is not complete while the stated shape is unmet.
ENDS_WITH_DIGITS = re.compile(
    r"numbers?\s+(?:at|in)\s+the\s+end|ends?\s+(?:with|in)\s+numbers?", re.IGNORECASE)
NOT_ENDS_WITH_DIGITS = re.compile(
    r"(?:no|not|doesn'?t|does\s+not|never)\s+(?:\w+\s+){0,3}numbers?\s+(?:at|in)\s+the\s+end",
    re.IGNORECASE)
NO_NUMBERS = re.compile(
    r"(?:no|not|doesn'?t|does\s+not|never)\s+(?:\w+\s+){0,3}(?:numbers?|digits?)",
    re.IGNORECASE)


def hint_rule(hints):
    """'ends-digit', 'not-ends-digit', 'no-digit', or None when it says nothing."""
    text = " ".join(hints or [])
    if not text:
        return None
    if NOT_ENDS_WITH_DIGITS.search(text):
        return "not-ends-digit"
    if ENDS_WITH_DIGITS.search(text):
        return "ends-digit"
    if NO_NUMBERS.search(text):
        return "no-digit"
    return None


def complete(code, hints):
    """True/False when a hint decides completeness; None when it says nothing."""
    rule = hint_rule(hints)
    if not code or rule is None:
        return None
    if rule == "ends-digit":
        # "this code has numbers at the end": anything ending in a letter still
        # has a segment to come, so it must not be treated as finished.
        return code[-1].isdigit()
    if rule == "not-ends-digit":
        return not code[-1].isdigit()
    if rule == "no-digit":
        return not any(character.isdigit() for character in code)
    return None


def merge_reading(accumulated, reading):
    """Fold a new screen reading into the code collected so far.

    Returns (code, kind). A reading that closely matches what we already have is
    an OCR correction of the same part, so it replaces it instead of being
    appended; only a clearly different reading starts a new part. This is what
    lets a code that arrives in messages seconds apart be assembled in order.
    """
    if not reading:
        return accumulated, "same"
    if not accumulated:
        return reading, "first"
    if reading == accumulated:
        return accumulated, "same"
    if reading.startswith(accumulated):
        return reading, "addition"
    if accumulated.startswith(reading):
        return accumulated, "stale"
    if difflib.SequenceMatcher(None, accumulated, reading).ratio() >= MERGE_SIMILARITY:
        return reading, "correction"
    return accumulated + reading, "append"


def fold_confusables(text):
    """Normalize the glyph pairs Windows OCR flattens, for a near-match score.

    `I`, `l` and `1` render differently in the game but collapse to one shape at
    bubble scale, as do `O` and `0`. Folding them measures "right code, wrong
    glyph" separately from a genuinely wrong reading.
    """
    return (text.replace("l", "I").replace("1", "I").replace("0", "O").upper())
