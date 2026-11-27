"""Score a session's preview with a six-part craft rubric (hierarchy, spacing, type, colour, motif, finish; 1-5
each), using any provider the app supports. The judge sees only the image and the brief, never which arm made it.
A model judging its own work tends to be generous; use a different model as the judge where you can.

The rubric is the one Vixl's own evaluation suite uses (``evals/judge.py`` in the Vixl repository), so scores here
and there mean the same thing.
"""

import base64
import json
import re

from .providers import ProviderError

RUBRIC = {
    "hierarchy": "Does one element read first, then a clear second and third? 1: everything the same weight or two rivals; "
                 "3: a leader, but the order after it is unclear; 5: the eye goes straight to one thing and the rest steps down.",
    "spacing": "Are gaps, margins and alignments deliberate? 1: ragged edges, holes and crowding; 3: mostly even with a "
               "few stray gaps; 5: one margin, one rhythm, space grouped by meaning.",
    "type": "Are size, weight and face used with intent? 1: one size and weight, or clashing faces; 3: steps exist but "
            "blur; 5: a clear scale, weights that carry meaning, comfortable line lengths.",
    "colour": "Is the palette restrained and doing a job? 1: clashing or muddy, or one flat colour; 3: pleasant but "
              "unfocused; 5: few colours, the accent used on what matters, strong contrast where it is needed.",
    "motif": "Is the imagery or ornament specific to the brief? 1: a generic stand-in, or none where the brief needs "
             "one; 3: relevant but plain; 5: specific, well drawn and tied to the subject.",
    "finish": "Does it read as finished, not merely correct? 1: placeholder look, flat default styling; 3: tidy but "
              "bare; 5: polished details, nothing left over, nothing missing.",
}
SYSTEM = ("You are a strict senior graphic designer judging a finished design from one image. Score each rubric "
          "dimension from 1 to 5 with whole numbers, judging only what you see. Reply with one JSON object and "
          "nothing else: {\"scores\": {\"hierarchy\": n, \"spacing\": n, \"type\": n, \"colour\": n, \"motif\": n, "
          "\"finish\": n}, \"notes\": \"two sentences on the weakest dimension\"}.")


class JudgeError(RuntimeError):
    """The judge could not score a preview: an unreadable reply or a failed call."""


def rubric_text():
    return "\n".join(f"- {name}: {text}" for name, text in RUBRIC.items())


def parse(text):
    """``{dimension: 1-5}`` and the notes from a judge's reply (the first JSON object in it)."""
    match = re.search(r"\{.*\}", text or "", re.S)
    if match is None:
        raise JudgeError(f"the judge's reply has no JSON object: {text[:120]!r}")
    try:
        reply = json.loads(match.group(0))
        scores = reply["scores"]
    except (ValueError, KeyError, TypeError) as exc:
        raise JudgeError(f"the judge's reply is not {{scores, notes}}: {text[:120]!r}") from exc
    if not isinstance(scores, dict) or set(scores) != set(RUBRIC) or not all(
            type(value) is int and 1 <= value <= 5 for value in scores.values()):
        raise JudgeError(f"scores must be whole numbers 1-5 for {', '.join(RUBRIC)}: {scores!r}")
    return scores, str(reply.get("notes", ""))[:600]


def judge_session(chat_factory, config, brief, png):
    try:
        chat = chat_factory(config)
        chat.start(SYSTEM, [])
        chat.add_user([
            {"type": "image", "media_type": "image/png", "data": base64.b64encode(png).decode("ascii")},
            {"type": "text", "text": f"The brief the designer was given:\n{brief}\n\nRubric (1-5 each):\n"
                                     f"{rubric_text()}\n\nScore the design in the image."},
        ])
        scores, notes = parse(chat.step().text)
    except (JudgeError, ProviderError) as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - a failed judgment is recorded, not raised
        return {"error": f"{type(exc).__name__}: {exc}"}
    return {"scores": scores, "mean": round(sum(scores.values()) / len(scores), 2), "notes": notes}
