"""Break each guard on purpose, and check it goes red.

A check is evidence only after it has been seen to fail: reading one says
what it was meant to catch, not what it catches. This repository has the
shape that produces inert tests: a stand-in engine and a codec.

Each entry below names a guard, the one-line change that should defeat it,
and the test that must fail when it does. Running this applies each change,
runs that test, requires a failure, and puts the file back. It writes
`walkthrough/mutations.md` with what happened.

    uv run python walkthrough/mutate.py

It edits files in the working tree and restores them in a `finally`. Run it
on a clean tree so `git status` is the backstop if it is interrupted.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "walkthrough" / "mutations.md"


@dataclass
class Mutation:
    name: str
    path: str
    find: str
    replace: str
    catches: str
    """The test that must fail. A mutation nothing catches is the finding."""

    why: str
    """What would be silently broken in the real thing if nothing caught it."""


MUTATIONS = [
    Mutation(
        name="the codec ignores its input",
        path="vox/engine.py",
        find='payload = MAGIC + len(text.encode("utf-8")).to_bytes(4, "big") + text.encode("utf-8")',
        replace='payload = MAGIC + len(b"x").to_bytes(4, "big") + b"x"',
        catches="tests/test_engine.py::test_text_survives_the_file",
        why="Every WAV would be identical, so the determinism test would pass and the loop would close on the wrong words.",
    ),
    Mutation(
        name="the codec stops being deterministic",
        path="vox/engine.py",
        find='        wav.setframerate(SAMPLE_RATE)\n        wav.writeframes(frames)',
        replace='        wav.setframerate(SAMPLE_RATE)\n        import time; wav.writeframes(frames + int(time.time_ns()).to_bytes(8, "big"))',
        catches="walkthrough/test_closed_loop.py::test_two_runs_produce_the_same_bytes",
        why="The whole deterministic claim. A clock anywhere in the path and two runs stop agreeing.",
    ),
    Mutation(
        name="the engine accepts audio it did not write",
        path="vox/engine.py",
        find='    if not payload.startswith(MAGIC):',
        replace='    if False:',
        catches="tests/test_engine.py::test_it_refuses_audio_it_did_not_write",
        why="A recording of a real voice would come back as whatever its samples spelled, answered 200, with nobody told.",
    ),
    Mutation(
        name="the engine leaves its own directory",
        path="vox/engine.py",
        find='            if candidate.is_relative_to(base.resolve()) and candidate.is_file():',
        replace='            if candidate.is_file():',
        catches="tests/test_engine_contract.py::test_transcribe_refuses_to_leave_its_directory",
        why="A real engine refuses a path escape. A stand-in that allows it passes a caller's traversal defect straight through to the real engine.",
    ),
    Mutation(
        name="the engine stops saying what it is",
        path="vox/engine.py",
        find='self._send(200, {"status": "ok", "engine": "vox.engine"})',
        replace='self._send(200, {"status": "ok"})',
        catches="tests/test_engine_contract.py::test_it_says_what_it_is",
        why="A caller could not tell the stand-in from a real engine somebody left running on the same workstation.",
    ),
    Mutation(
        name="the client ignores its contract and hardcodes a path",
        path="vox/stt.py",
        find="            self._url(self.contract.transcribe),",
        replace='            self._url("/api/voice/transcribe"),',
        catches="tests/test_contract.py::test_the_wire_used_this_contract_s_paths",
        why="The whole agnosticism claim. A default that is the only value ever honoured is a hardcoding with a longer spelling, and everything still passes against the default engine.",
    ),
    Mutation(
        name="the client ignores its contract and hardcodes a response key",
        path="vox/stt.py",
        find="        return resp.json()[self.contract.text_key]",
        replace='        return resp.json()["text"]',
        catches="tests/test_contract.py::test_the_loop_closes_on_either_contract",
        why="Same claim from the other side: reading a fixed key works against every engine that happens to spell it that way, and no others.",
    ),
    Mutation(
        name="the engine answers a fixed path rather than its contract's",
        path="vox/engine.py",
        find="        if route.path == contract.transcribe:",
        replace='        if route.path == "/api/voice/transcribe":',
        catches="tests/test_contract.py::test_the_loop_closes_on_either_contract",
        why="The stand-in would only ever stand in for one engine, so a test claiming to exercise another would be exercising nothing.",
    ),
    Mutation(
        name="the loop stops going over the wire",
        path="vox/session.py",
        find="        echoed = self.stt.transcribe_file(echo_as)",
        replace="        echoed = heard",
        catches="walkthrough/test_closed_loop.py::test_it_went_over_the_wire",
        why="`closed` would be True by construction. The loop would report success without ever reading back what it wrote.",
    ),
    Mutation(
        name="speaking stops writing a file",
        path="vox/tts.py",
        find="        Path(out_path).parent.mkdir(parents=True, exist_ok=True)\n        return encode_wav(text, out_path)",
        replace="        Path(out_path).parent.mkdir(parents=True, exist_ok=True)\n        return str(out_path)",
        catches="walkthrough/test_closed_loop.py::test_the_audio_is_a_real_file_written_this_run",
        why="With a leftover file from a previous run the loop still closes. This is the stale-artifact hole, and it is why the walkthrough uses a fresh directory.",
    ),
    Mutation(
        name="the synthesizer stops being deterministic",
        path="vox/synth.py",
        find='    rng = random.Random(hashlib.sha256(text.encode("utf-8")).digest())',
        replace="    rng = random.Random()",
        catches="tests/test_synth.py::test_the_same_text_gives_the_same_bytes",
        why="An unseeded rng puts a clock in the fricatives, so the same words produce a different file every run and any recorded artifact churns.",
    ),
    Mutation(
        name="the synthesizer ignores its input",
        path="vox/synth.py",
        find="    words = [w for w in text.split() if w.strip()]",
        replace='    words = ["aa"]',
        catches="tests/test_synth.py::test_different_text_gives_different_bytes",
        why="One sound for every phrase. The determinism test passes perfectly against it, which is why that test is not enough on its own.",
    ),
    Mutation(
        name="the client drops the pause it was asked to send",
        path="vox/stt.py",
        find="        if self.contract.pause_param and pause_ms is not None:\n            params[self.contract.pause_param] = pause_ms",
        replace="        pass",
        catches="tests/test_stt.py::test_listen_sends_the_pause_when_the_contract_names_it_and_a_value_is_given",
        why="A caller asking for a longer pause would get the engine's default and a sentence-length answer cut off mid-thought, with nothing failing.",
    ),
    Mutation(
        name="the client sends the pause whether or not the contract names it",
        path="vox/stt.py",
        find="        if self.contract.pause_param and pause_ms is not None:\n            params[self.contract.pause_param] = pause_ms",
        replace='        if pause_ms is not None:\n            params[self.contract.pause_param or "pause_ms"] = pause_ms',
        catches="tests/test_stt.py::test_listen_sends_no_pause_on_a_contract_without_one",
        why="An engine with no such parameter would be sent one under a name it never agreed to, and the seam would hardcode a spelling.",
    ),
    Mutation(
        name="the client drops the hint it was asked to send",
        path="vox/stt.py",
        find="        if self.contract.hint_param and hint:\n            params[self.contract.hint_param] = \", \".join(hint)",
        replace="        pass",
        catches="tests/test_contract.py::test_a_hint_travels_in_this_contract_s_spelling_or_not_at_all",
        why="A short answer would be transcribed with no idea of the words it should be, and nothing would fail.",
    ),
    Mutation(
        name="the client sends the hint whether or not the contract names it",
        path="vox/stt.py",
        find="        if self.contract.hint_param and hint:\n            params[self.contract.hint_param] = \", \".join(hint)",
        replace='        if hint:\n            params[self.contract.hint_param or "hint"] = ", ".join(hint)',
        catches="tests/test_contract.py::test_a_hint_travels_in_this_contract_s_spelling_or_not_at_all",
        why="An engine with no such parameter would be sent one under a name it never agreed to.",
    ),
    Mutation(
        name="an announcement drops the question's options",
        path="vox/stt.py",
        find='        if options:\n            body["options"] = list(options)',
        replace="        pass",
        catches="tests/test_stt.py::test_announce_carries_a_question_s_options_in_the_order_given",
        why="A display could no longer offer the answers as controls, so a person who cannot be heard has no other way to answer.",
    ),
    Mutation(
        name="the SAPI path plays through pyttsx3's say",
        path="vox/adapters/pyttsx3.py",
        find="        if _sapi():\n            self.cut = _sapi_speak(text, out_path, self.playback, self.output_device, until)\n            return out_path\n",
        replace="",
        catches="tests/test_adapters.py::test_on_sapi_the_file_is_written_then_played_synchronously",
        why="On Windows a sentence would be played through pyttsx3's say instead of SAPI's synchronous SpeakStream, and speak could return before it had been heard.",
    ),
    Mutation(
        name="a named output that matches several is guessed",
        path="vox/adapters/pyttsx3.py",
        find="    if len(matches) != 1:",
        replace="    if not matches:",
        catches="tests/test_adapters.py::test_on_sapi_an_output_matching_several_or_none_is_refused",
        why="A fragment naming several outputs would play to whichever came first, which can be a jack nobody hears.",
    ),
    Mutation(
        name="a listen's confidence is read under a fixed name",
        path="vox/stt.py",
        find="        reported = data.get(self.contract.confidence_key) if self.contract.confidence_key else None",
        replace='        reported = data.get("confidence")',
        catches="tests/test_contract.py::test_a_confidence_is_read_in_this_contract_s_spelling_or_not_at_all",
        why="A caller deciding whether to ask for confirmation would read nothing from an engine that spells it differently, or something from one that never said.",
    ),
    Mutation(
        name="the engine accepts a pause the engine it stands in for refuses",
        path="vox/engine.py",
        find="        if pause is not None and not 100 <= pause <= 5000:",
        replace="        if False:",
        catches="tests/test_engine_contract.py::test_listen_refuses_a_silence_ms_joe_refuses",
        why="A caller sending a pause outside the real engine's bounds would pass the offline suite and be answered 400 at the demo, which is the drift the stand-in exists to prevent.",
    ),
    Mutation(
        name="the engine refuses the pauses at the edges of the bound it stands in for",
        path="vox/engine.py",
        find="        if pause is not None and not 100 <= pause <= 5000:",
        replace="        if pause is not None and not 100 < pause < 5000:",
        catches="tests/test_engine_contract.py::test_listen_accepts_the_pauses_at_the_edges_of_joes_bound",
        why="Every refusal case still passes against an exclusive bound; only a test sending the edges themselves tells an inclusive bound from one off by one.",
    ),
    Mutation(
        name="every call builds its own client",
        path="vox/stt.py",
        find="        if self._client is None:\n            self._client = httpx.Client(timeout=self.timeout)\n        return self._client",
        replace="        return httpx.Client(timeout=self.timeout)",
        catches="tests/test_stt.py::test_one_client_serves_every_call",
        why="Nothing breaks: each call pays for a new client and its SSL context, and the loop slows with no other symptom.",
    ),
    Mutation(
        name="a question is never cut short",
        path="vox/adapters/pyttsx3.py",
        find="            if _asked(until):",
        replace="            if False:",
        catches="tests/test_adapters.py::test_on_sapi_a_sentence_stops_as_soon_as_until_says_so",
        why="A person who answers before the question ends is talked over, and the answer waits for a voice that has stopped mattering.",
    ),
    Mutation(
        name="a failing check stops the question",
        path="vox/adapters/pyttsx3.py",
        find="    except Exception:  # noqa: BLE001 -- a check that fails never cuts a sentence\n        return False",
        replace="    except Exception:  # noqa: BLE001 -- a check that fails never cuts a sentence\n        return True",
        catches="tests/test_adapters.py::test_on_sapi_an_until_that_fails_never_cuts_the_sentence",
        why="An engine that goes away mid-question would cut every question short, and the consent would never be heard in full.",
    ),
    Mutation(
        name="an interruption is read under a fixed name",
        path="vox/stt.py",
        find="            return resp.status_code == 200 and bool(resp.json().get(self.contract.interrupted_key))",
        replace='            return resp.status_code == 200 and bool(resp.json().get("interrupted"))',
        catches="tests/test_contract.py::test_an_interruption_is_read_in_this_contract_s_spelling_or_not_at_all",
        why="An engine that spells it differently could never stop a question, however the person answered.",
    ),
    Mutation(
        name="a watch is opened without the listen's parameters",
        path="vox/stt.py",
        find="                self._url(self.contract.watch), params=self._listen_params(duration, pause_ms, hint),",
        replace="                self._url(self.contract.watch), params={},",
        catches="tests/test_stt.py::test_watch_posts_the_listen_s_parameters_briefly",
        why="The take opened over the question would be transcribed without the answer's hint and ended on the engine's default pause.",
    ),
    Mutation(
        name="an engine that cannot be asked is taken as an interruption",
        path="vox/stt.py",
        find="        except (httpx.HTTPError, ValueError):\n            return False\n\n    def devices",
        replace="        except (httpx.HTTPError, ValueError):\n            return True\n\n    def devices",
        catches="tests/test_stt.py::test_interrupted_is_false_whenever_the_engine_cannot_say",
        why="A question would be cut short on a guess whenever the engine stalled.",
    ),
    Mutation(
        name="the media client asks a fixed transcribe route rather than its contract's",
        path="vox/media.py",
        find='        route = self._offered("media_transcribe", "transcripts")',
        replace='        route = self._offered("media_transcribe", "transcripts") and "/api/media/{media_id}/transcribe"',
        catches="tests/test_contract.py::test_the_media_wire_used_this_contract_s_spelling",
        why="Every engine that spells its media surface differently would be asked for a route it does not have, and only the default's would ever answer.",
    ),
    Mutation(
        name="a segment's time is read under a fixed key",
        path="vox/media.py",
        find='        start=_number(_key(body, c.start_key, where), f"{where}.{c.start_key}"),\n        end=_number(_key(body, c.end_key, where), f"{where}.{c.end_key}"),\n        text=_text(',
        replace='        start=_number(_key(body, "start", where), f"{where}.{c.start_key}"),\n        end=_number(_key(body, c.end_key, where), f"{where}.{c.end_key}"),\n        text=_text(',
        catches="tests/test_contract.py::test_the_media_surface_answers_on_either_contract",
        why="A transcript from an engine that times its segments under another name would be refused as malformed, and captions from it would never be made.",
    ),
    Mutation(
        name="a refusal's reason is read under a fixed key",
        path="vox/media.py",
        find="        if isinstance(body, Mapping) and self.contract.detail_key in body:\n            detail = body[self.contract.detail_key]",
        replace='        if isinstance(body, Mapping) and "detail" in body:\n            detail = body["detail"]',
        catches="tests/test_contract.py::test_a_refusal_s_reason_is_read_in_this_contract_s_spelling",
        why="An engine that says why under another name would have its reason buried in its raw body.",
    ),
    Mutation(
        name="a refusal is raised without the engine's reason",
        path="vox/media.py",
        find="            raise EngineRefused(resp.status_code, self._detail(resp), self.base_url, request)",
        replace="            raise EngineRefused(resp.status_code, resp.reason_phrase, self.base_url, request)",
        catches="tests/test_media.py::test_a_refusal_carries_the_engine_s_status_and_reason",
        why="A person told a caption job failed would read 'Unsupported Media Type' and not which type, or what the engine takes.",
    ),
    Mutation(
        name="a route the contract leaves out is asked anyway",
        path="vox/media.py",
        find="        route = getattr(self.contract, name)\n        if not route:\n            raise EngineDoesNotOffer(capability, self.base_url, name)\n        return route",
        replace="        return getattr(self.contract, name) or getattr(EngineContract(), name)",
        catches="tests/test_media.py::test_a_route_the_contract_leaves_out_is_refused_by_name_before_any_request",
        why="An engine with no such route would be sent the default engine's, and the failure would read as a malformed answer rather than a missing capability.",
    ),
    Mutation(
        name="a language the engine cannot be asked for is dropped",
        path="vox/media.py",
        find='            if not self.contract.language_param:\n                raise EngineDoesNotOffer("a choice of language", self.base_url, "language_param")\n            params[self.contract.language_param] = language',
        replace="            if self.contract.language_param:\n                params[self.contract.language_param] = language",
        catches="tests/test_media.py::test_a_language_is_refused_on_a_contract_that_cannot_ask_for_one",
        why="A transcript would come back in whichever language the engine guessed, as if it were the one asked for.",
    ),
    Mutation(
        name="words are asked for under a fixed name",
        path="vox/media.py",
        find='        if self.contract.words_param:\n            params[self.contract.words_param] = "true" if words else "false"',
        replace='        params["words"] = "true" if words else "false"',
        catches="tests/test_media.py::test_words_travel_only_where_the_contract_names_the_parameter",
        why="An engine with no such parameter would be sent one under a name it never agreed to.",
    ),
    Mutation(
        name="a feature is asked for by vox's name rather than the engine's",
        path="vox/media.py",
        find='            names = (getattr(self.contract, f"{name}_feature") for name in asked)',
        replace="            names = asked",
        catches="tests/test_media.py::test_a_feature_is_asked_for_by_the_engine_s_name_and_read_back_by_vox_s",
        why="An engine whose name for a feature differs from vox's would refuse the analysis outright.",
    ),
    Mutation(
        name="an upload's id goes into its route unescaped",
        path="vox/media.py",
        find='        return route.replace(MEDIA_ID, quote(self._id(handle), safe=""))',
        replace="        return route.replace(MEDIA_ID, self._id(handle))",
        catches="tests/test_media.py::test_an_id_is_escaped_whole_into_its_route",
        why="An id with a separator or a query in it would address another route on the engine.",
    ),
    Mutation(
        name="leaving the block leaves the uploads behind",
        path="vox/media.py",
        find="        discarded = [self._discard(media_id) for media_id in list(self._held)]",
        replace="        discarded = []",
        catches="tests/test_media.py::test_leaving_the_block_discards_what_this_engine_still_holds",
        why="Every caption job would leave its mixed sound on the engine until the engine's own clock took it.",
    ),
    Mutation(
        name="a missing key in an answer is read as null",
        path="vox/media.py",
        find='    if key not in body:\n        raise _Malformed(f"{where} has no {key!r}")\n    return body[key]',
        replace="    return body.get(key)",
        catches="tests/test_media.py::test_an_answer_that_is_not_the_contract_s_shape_is_malformed_and_says_where",
        why="A contract spelling a key wrong would read as an engine with nothing to say, rather than as a mismatch to fix.",
    ),
    Mutation(
        name="the stand-in answers the default media routes rather than its contract's",
        path="vox/engine.py",
        find="    for name, verb, attribute in _MEDIA_ROUTES:\n        template = getattr(contract, attribute)",
        replace="    for name, verb, attribute in _MEDIA_ROUTES:\n        template = getattr(EngineContract(), attribute)",
        catches="tests/test_contract.py::test_the_media_surface_answers_on_either_contract",
        why="The stand-in would stand in for one engine's media surface, so a test claiming to exercise another would exercise nothing.",
    ),
    Mutation(
        name="the stand-in takes an upload of a type its engine refuses",
        path="vox/engine.py",
        find="    if kind not in state.media_formats:",
        replace="    if False:",
        catches="tests/test_engine_contract.py::test_a_media_upload_refuses_what_v1_refuses_with_a_detail",
        why="A caller labelling its upload wrongly would pass the offline suite and be answered 415 by the real engine.",
    ),
    Mutation(
        name="the stand-in hands an upload's id out twice",
        path="vox/engine.py",
        find='    handle = MediaHandle(f"m{next(state._ids)}", frames / rate, rate, channels, True)',
        replace='    handle = MediaHandle(f"m{len(state.uploads) + 1}", frames / rate, rate, channels, True)',
        catches="tests/test_engine_contract.py::test_a_media_id_is_never_reused",
        why="A caller holding a discarded upload's id would reach somebody else's upload with it.",
    ),
    Mutation(
        name="the stand-in reports a length it did not read from the upload",
        path="vox/engine.py",
        find='    handle = MediaHandle(f"m{next(state._ids)}", frames / rate, rate, channels, True)',
        replace='    handle = MediaHandle(f"m{next(state._ids)}", 1.0, rate, channels, True)',
        catches="walkthrough/test_media_engine.py::test_the_narration_goes_up_with_its_real_length",
        why="Captions clamped to an upload's length would be clamped to a number the stand-in made up, and pass.",
    ),
    Mutation(
        name="narration written by the platform voice is also played",
        path="vox/adapters/pyttsx3.py",
        find="            _sapi_speak(text, out_path, False, None)",
        replace="            _sapi_speak(text, out_path, True, None)",
        catches="tests/test_adapters.py::test_on_sapi_to_wav_writes_the_file_and_plays_nothing_whatever_playback_says",
        why="Making narration for a video would read every line aloud through the speakers of whoever made it.",
    ),
    Mutation(
        name="narration is written to the synthesizer's own directory",
        path="vox/synth.py",
        find="        return write_wav(synthesize(text, self.rate), path, self.rate)",
        replace='        return write_wav(synthesize(text, self.rate), os.path.join(self.out_dir, "narration.wav"), self.rate)',
        catches="tests/test_tts.py::test_it_writes_the_wav_where_it_is_told_and_says_where",
        why="A timeline would lay a path under its video that holds nothing, while the narration sat elsewhere under one fixed name.",
    ),
]


def _run(test: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", test, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def main() -> int:
    print("baseline: running the tests these mutations target")
    baseline = _run_all(m.catches for m in MUTATIONS)
    if baseline.returncode != 0:
        # A mutation run against a red baseline proves nothing either way.
        print(baseline.stdout[-3000:])
        print("BASELINE IS RED — fix that first; every result below would be meaningless.")
        return 1
    print("baseline green\n")

    rows = []
    for mutation in MUTATIONS:
        target = ROOT / mutation.path
        # Bytes, not text, on both sides. `write_text` translates newlines to
        # the platform separator, so an LF file restored through it on Windows
        # comes back as CRLF: the same content, different bytes, and the file
        # reported modified.
        original = target.read_bytes()
        source = original.decode("utf-8").replace("\r\n", "\n")
        if mutation.find not in source:
            rows.append((mutation, "STALE", "the mutation no longer matches the source"))
            print(f"[STALE] {mutation.name}: text not found in {mutation.path}")
            continue
        try:
            target.write_bytes(source.replace(mutation.find, mutation.replace, 1).encode("utf-8"))
            result = _run(mutation.catches)
        finally:
            target.write_bytes(original)
            # Assert the restore rather than trusting it: this harness edits
            # tracked source, so a restore that is not byte-for-byte stops the
            # run.
            if target.read_bytes() != original:
                raise SystemExit(f"{mutation.path} was not restored byte-for-byte; check `git diff`")

        if result.returncode == 0:
            rows.append((mutation, "INERT", "the test passed against the broken code"))
            print(f"[INERT] {mutation.name}: {mutation.catches} passed anyway")
        else:
            rows.append((mutation, "caught", mutation.catches))
            print(f"[caught] {mutation.name}")

    _write_report(rows)
    bad = [r for r in rows if r[1] != "caught"]
    print(f"\n{len(rows) - len(bad)}/{len(rows)} mutations caught; report at {REPORT.relative_to(ROOT)}")
    return 1 if bad else 0


def _run_all(tests) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", *sorted(set(tests)), "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def _write_report(rows) -> None:
    lines = [
        "# Mutations, and what caught them",
        "",
        "Recorded by `uv run python walkthrough/mutate.py`. Each row is a guard",
        "that has been seen to fail: the change was applied, the named test went",
        "red, and the file was put back. A guard nobody has broken is a guard",
        "nobody has tested.",
        "",
        "| Mutation | Caught by | What it would have hidden |",
        "|---|---|---|",
    ]
    for mutation, status, detail in rows:
        caught = f"`{mutation.catches}`" if status == "caught" else f"**{status}** — {detail}"
        lines.append(f"| {mutation.name} | {caught} | {mutation.why} |")
    lines.append("")
    # newline pinned for the same reason as the walkthrough's artifact: text
    # mode would translate to the platform separator, and the drift check that
    # guards this file compares bytes.
    REPORT.write_text("\n".join(lines), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    raise SystemExit(main())
