"""Check the panel voice grammars against Home Assistant's built-in sentences.

Home Assistant compiles every sentence trigger into one set and tries it
before its built-in intents, so a trigger phrase that a built-in intent also
understands silently takes that request over. This module answers, exactly
and for every locale at once, whether that can happen:

- ``stolen(lang, grammars)``: is there a sentence that both a built-in intent
  of ``lang`` and one of our trigger templates (from any locale) accept? Our
  ``{page}`` slot is any text; a built-in name, area, floor or number slot is
  one opaque value the user said. This is the direction that decides takeover.
- ``claimed(lang, grammar)``: does a built-in intent of ``lang`` understand one
  of our own sentences when its name, area and floor slots may be any text?
  A match only counts when the built-in template contributes at least one
  literal word, so a bare ``{name}`` template (a script or scene said by name)
  does not count against every phrase.

Both are intersections of the two grammars' finite automata (hassil's own
``fst`` compilation), compared character by character with spaces ignored, so
no sampling is involved and the result does not depend on how many sentences
a locale's templates expand to.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import home_assistant_intents
from hassil import Intents
from hassil.expression import ListReference
from hassil.fst import EPS, SPACE, Fst, intents_to_fst
from hassil.intents import RangeSlotList, TextSlotList
from hassil.sample import sample_expression
import yaml

SENTENCES = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "panel_assistant"
    / "sentences"
)

# Our locale names against home_assistant_intents' data files. Home Assistant
# loads zh-CN for a zh-Hans instance (language_util.matches prefers the region
# with the same script); the others are identical.
BUILTIN_LANGUAGE = {"zh-Hans": "zh-CN"}

SLOT = "\x00"  # one opaque slot value
_WILD = object()
_NUMBER: list[str] = []  # marker: a number range slot


@dataclass(frozen=True)
class Collision:
    """One built-in intent and one of our commands that accept the same text."""

    builtin_intent: str
    command: str
    example: str


def grammar_languages() -> list[str]:
    return sorted({path.stem for path in SENTENCES.glob("*.yaml")} | set(DRAFTS))


# A draft file to check in place of (or beside) the committed one, by locale.
DRAFTS: dict[str, Path] = {}


@cache
def load_grammar(language: str) -> dict[str, Any]:
    path = DRAFTS.get(language, SENTENCES / f"{language}.yaml")
    return yaml.safe_load(path.read_text("utf-8"))


@cache
def builtin_intents(language: str) -> Intents:
    data = home_assistant_intents.get_intents(BUILTIN_LANGUAGE.get(language, language))
    assert data is not None, f"no built-in sentences for {language}"
    return Intents.from_dict(data)


def trigger_intents(languages: tuple[str, ...]) -> Intents:
    """Our commands as Home Assistant compiles them: one intent per command."""
    intents: dict[str, Any] = {}
    for language in languages:
        for command, spec in load_grammar(language)["commands"].items():
            data = intents.setdefault(command, {"data": [{"sentences": []}]})
            data["data"][0]["sentences"].extend(spec["sentences"])
    return Intents.from_dict({"language": "en", "intents": intents})


class _Automaton:
    """A character automaton over one hassil FST, slots as single symbols."""

    def __init__(
        self,
        fst: Fst,
        slot_is_wild: bool,
        fixed: dict[str, list[str]],
        numbers: set[str],
    ) -> None:
        self.start = fst.start
        self.final = fst.final_states
        self.slot_is_wild = slot_is_wild
        # Slots with a fixed vocabulary (colours, durations) spell out their
        # values; a number range is one opaque value; anything else (a name,
        # area, floor, page or free text) is the user's own words.
        self.fixed = fixed
        self.numbers = numbers
        # state -> [(label, to)], label "" for epsilon, SLOT, or one character;
        # multi-character words become chains of fresh states.
        self.arcs: dict[Any, list[tuple[Any, Any]]] = {}
        for state, arcs in fst.arcs.items():
            for arc in arcs:
                self._add_word(state, arc.in_label, arc.to_state)

    def _add_word(self, state: Any, word: str, to: Any) -> None:
        out = self.arcs.setdefault(state, [])
        if word in (EPS, SPACE):
            out.append(("", to))
            return
        if word.startswith("{") and word.endswith("}"):
            slot = word[1:-1]
            if slot in self.fixed:
                for value in self.fixed[slot]:
                    self._add_word(state, value, to)
            elif slot in self.numbers:
                out.append((SLOT, to))
            else:
                out.append((_WILD if self.slot_is_wild else SLOT, to))
            return
        if word == "<skip>":
            return
        chars = [c for c in word.casefold() if not c.isspace()]
        if not chars:
            out.append(("", to))
            return
        prev = state
        for index, char in enumerate(chars):
            nxt = to if index == len(chars) - 1 else (state, word, to, index)
            self.arcs.setdefault(prev, []).append((char, nxt))
            prev = nxt


def _intersect(
    left: _Automaton, right: _Automaton, require_left_literal: bool
) -> str | None:
    """Shortest common string, or None. ``_WILD`` consumes one or more symbols.

    A wildcard arc loops: entering it consumes one symbol and stays "inside"
    until it chooses to leave, so the state carries an inside flag per side.
    """
    start = (left.start, False, right.start, False, False)
    seen = {start}
    queue: deque[tuple[tuple[Any, ...], str]] = deque([(start, "")])

    def moves(auto: _Automaton, state: Any, inside: bool):
        """(label, to_state, to_inside, literal) moves, label None = epsilon."""
        if inside:
            yield (None, state, False, False)  # leave the wildcard
        for label, to in auto.arcs.get(state, ()):
            if label == "":
                yield (None, to, False, False)
            elif label is _WILD:
                yield (_WILD, to, True, False)
            else:
                yield (label, to, False, label != SLOT)

    while queue:
        (ls, lin, rs, rin, lit), text = queue.popleft()
        if (
            ls in left.final
            and rs in right.final
            and not lin
            and not rin
            and (lit or not require_left_literal)
        ):
            return text
        nexts = []
        for llabel, lto, lto_in, llit in moves(left, ls, lin):
            if llabel is None:
                nexts.append(((lto, lto_in, rs, rin, lit), text))
        for rlabel, rto, rto_in, _ in moves(right, rs, rin):
            if rlabel is None:
                nexts.append(((ls, lin, rto, rto_in, lit), text))
        # Inside a wildcard a side may consume any symbol without moving.
        lsym = list(moves(left, ls, lin)) + ([(_WILD, ls, True, False)] if lin else [])
        rsym = list(moves(right, rs, rin)) + ([(_WILD, rs, True, False)] if rin else [])
        for llabel, lto, lto_in, llit in lsym:
            if llabel is None:
                continue
            for rlabel, rto, rto_in, _ in rsym:
                if rlabel is None:
                    continue
                if llabel is _WILD and rlabel is _WILD:
                    char = SLOT
                elif llabel is _WILD:
                    char = rlabel
                elif rlabel is _WILD:
                    char = llabel
                elif llabel == rlabel:
                    char = llabel
                else:
                    continue
                nexts.append(
                    (
                        (lto, lto_in, rto, rto_in, lit or llit),
                        text + ("…" if char == SLOT else char),
                    )
                )
        for state, new_text in nexts:
            if state not in seen:
                seen.add(state)
                queue.append((state, new_text))
    return None


def _list_names(intents: Intents) -> set[str]:
    names: set[str] = set()

    def walk(expression: Any) -> None:
        if isinstance(expression, ListReference):
            names.add(expression.list_name)
        for item in getattr(expression, "items", None) or ():
            walk(item)

    for intent in intents.intents.values():
        for data in intent.data:
            for sentence in data.sentences:
                walk(sentence.expression)
    for rule in intents.expansion_rules.values():
        walk(rule.expression)
    return names


def _slot_vocabulary(intents: Intents) -> dict[str, list[str]]:
    """Slot name -> spoken values, for lists with a fixed vocabulary.

    fst writes a list reference as its slot name, so this maps slot names.
    Number ranges map to ``_NUMBER``; user-named lists are absent.
    """
    vocabulary: dict[str, list[str]] = {}

    def walk(expression: Any) -> None:
        if isinstance(expression, ListReference):
            slot_list = intents.slot_lists.get(expression.list_name)
            if isinstance(slot_list, RangeSlotList):
                vocabulary[expression.slot_name] = _NUMBER
            elif isinstance(slot_list, TextSlotList) and expression.list_name not in (
                "name",
                "area",
                "floor",
            ):
                spoken = vocabulary.setdefault(expression.slot_name, [])
                for value in slot_list.values:
                    for text in sample_expression(value.text_in):
                        spoken.append(text)
        for item in getattr(expression, "items", None) or ():
            walk(item)

    for intent in intents.intents.values():
        for data in intent.data:
            for sentence in data.sentences:
                walk(sentence.expression)
    for rule in intents.expansion_rules.values():
        walk(rule.expression)
    return vocabulary


def _automata(intents: Intents, slot_is_wild: bool) -> dict[str, _Automaton]:
    """One automaton per intent; every list becomes a slot token.

    hassil's FST keeps text and range lists as ``{name}`` tokens but silently
    drops a sentence whose list is a wildcard or undefined (``{page}``,
    ``{message}``), so those are given a stand-in text list first.
    """
    stand_ins: dict[str, Any] = {
        name: TextSlotList.from_strings(["x"])
        for name in _list_names(intents)
        if not isinstance(intents.slot_lists.get(name), (TextSlotList, RangeSlotList))
    }
    vocabulary = _slot_vocabulary(intents)
    numbers = {slot for slot, values in vocabulary.items() if values is _NUMBER}
    fixed = {slot: values for slot, values in vocabulary.items() if slot not in numbers}
    return {
        name: _Automaton(
            intents_to_fst(intents, slot_lists=stand_ins, intent_names=[name]),
            slot_is_wild,
            fixed,
            numbers,
        )
        for name in intents.intents
    }


def _builtin_automata(language: str, slot_is_wild: bool) -> dict[str, _Automaton]:
    return _automata(builtin_intents(language), slot_is_wild)


def _command_automata(intents: Intents, slot_is_wild: bool) -> dict[str, _Automaton]:
    return _automata(intents, slot_is_wild)


def stolen(language: str, languages: tuple[str, ...]) -> list[Collision]:
    """Built-in requests of ``language`` that our merged triggers would take."""
    ours = _command_automata(trigger_intents(languages), slot_is_wild=True)
    found = []
    for intent, builtin in _builtin_automata(language, slot_is_wild=False).items():
        for command, mine in ours.items():
            if (example := _intersect(builtin, mine, False)) is not None:
                found.append(Collision(intent, command, example))
    return found


def claimed(language: str) -> list[Collision]:
    """Our sentences of ``language`` a built-in intent would also understand."""
    ours = _command_automata(trigger_intents((language,)), slot_is_wild=False)
    found = []
    for intent, builtin in _builtin_automata(language, slot_is_wild=True).items():
        for command, mine in ours.items():
            if (example := _intersect(builtin, mine, True)) is not None:
                found.append(Collision(intent, command, example))
    return found


def overlaps(languages: tuple[str, ...]) -> list[Collision]:
    """Pairs of our commands that accept the same text, which would fire both."""
    ours = _command_automata(trigger_intents(languages), slot_is_wild=True)
    names = sorted(ours)
    found = []
    for index, first in enumerate(names):
        for second in names[index + 1 :]:
            if (example := _intersect(ours[first], ours[second], False)) is not None:
                found.append(Collision(first, second, example))
    return found


SAMPLES = Path(__file__).resolve().parent / "fixtures" / "voice_samples"


SAMPLE_DRAFTS: dict[str, Path] = {}


def load_samples(language: str) -> dict[str, Any]:
    path = SAMPLE_DRAFTS.get(language, SAMPLES / f"{language}.yaml")
    return yaml.safe_load(path.read_text("utf-8"))


def screen_alias(language: str) -> str:
    """The screen light's translated name, which Panel Assistant adds as its alias."""
    import json

    translations = SENTENCES.parent / "translations" / f"{language}.json"
    return json.loads(translations.read_text("utf-8"))["entity"]["light"]["screen"]["name"]


def builtin_errors(language: str) -> list[str]:
    """The screen phrases reach the built-in on/off intents through the alias."""
    from hassil.intents import TextSlotList
    from hassil.recognize import recognize_all

    intents = builtin_intents(language)
    names = [screen_alias(language), *load_grammar(language).get("screen_names", ())]
    lists = {
        "name": TextSlotList.from_tuples(
            [(name, name, {"domain": "light"}) for name in names]
        ),
        "area": TextSlotList.from_strings(["Zorblat"]),
        "floor": TextSlotList.from_strings(["Zorblat"]),
    }
    want = {"screen_off": "HassTurnOff", "screen_on": "HassTurnOn"}
    errors = []
    for key, text in load_samples(language)["builtin"].items():
        got = {
            r.intent.name
            for r in recognize_all(
                text, intents, slot_lists=lists, intent_context={"area": "Zorblat"}
            )
        }
        if want[key] not in got:
            errors.append(f"built-in {key} {text!r}: got {sorted(got)}, want {want[key]}")
    return errors


def sample_errors(language: str, languages: tuple[str, ...]) -> list[str]:
    """Each must-match sample reaches exactly its command (with its page);
    each must-not-match sample reaches none of the merged triggers."""
    from hassil.intents import WildcardSlotList
    from hassil.recognize import recognize_all

    intents = trigger_intents(languages)
    wild = {name: WildcardSlotList(name) for name in _list_names(intents)}
    samples = load_samples(language)
    errors = []
    for sample in samples["match"]:
        results = list(recognize_all(sample["text"], intents, slot_lists=wild))
        commands = {result.intent.name for result in results}
        if commands != {sample["command"]}:
            errors.append(f"{sample['text']!r}: matched {sorted(commands)}")
            continue
        if "page" in sample:
            pages = {result.entities["page"].text.strip() for result in results}
            if sample["page"] not in pages:
                errors.append(f"{sample['text']!r}: page {sorted(pages)}")
    for text in samples["miss"]:
        if commands := {r.intent.name for r in recognize_all(text, intents, slot_lists=wild)}:
            errors.append(f"{text!r}: should match nothing, matched {sorted(commands)}")
    return errors


def report(language: str) -> list[str]:
    """Every check for one locale's grammar, against every locale's built-ins."""
    languages = tuple(grammar_languages())
    problems = [f"claimed by {c.builtin_intent}: {c.command} {c.example!r}" for c in claimed(language)]
    for builtin_language in languages:
        problems += [
            f"steals {builtin_language} {c.builtin_intent}: {c.command} {c.example!r}"
            for c in stolen(builtin_language, languages)
        ]
    problems += [f"overlap {c.builtin_intent}/{c.command}: {c.example!r}" for c in overlaps(languages)]
    if language in SAMPLE_DRAFTS or (SAMPLES / f"{language}.yaml").exists():
        problems += sample_errors(language, languages)
        problems += builtin_errors(language)
    else:
        problems.append(f"no samples file {SAMPLES / f'{language}.yaml'}")
    return problems


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("language")
    parser.add_argument("--grammar", type=Path, help="draft grammar for this locale")
    parser.add_argument("--samples", type=Path, help="draft samples for this locale")
    options = parser.parse_args()
    if options.grammar:
        DRAFTS[options.language] = options.grammar.resolve()
    if options.samples:
        SAMPLE_DRAFTS[options.language] = options.samples.resolve()
    problems = report(options.language)
    print("\n".join(problems) or "OK")
    sys.exit(1 if problems else 0)
