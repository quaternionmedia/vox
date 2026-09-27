# Mutations, and what caught them

Recorded by `uv run python walkthrough/mutate.py`. Each row is a guard
that has been seen to fail: the change was applied, the named test went
red, and the file was put back. A guard nobody has broken is a guard
nobody has tested.

| Mutation | Caught by | What it would have hidden |
|---|---|---|
| the codec ignores its input | `tests/test_engine.py::test_text_survives_the_file` | Every WAV would be identical, so the determinism test would pass and the loop would close on the wrong words. |
| the codec stops being deterministic | `walkthrough/test_closed_loop.py::test_two_runs_produce_the_same_bytes` | The whole deterministic claim. A clock anywhere in the path and two runs stop agreeing. |
| the engine accepts audio it did not write | `tests/test_engine.py::test_it_refuses_audio_it_did_not_write` | A recording of a real voice would come back as whatever its samples spelled, answered 200, with nobody told. |
| the engine leaves its own directory | `tests/test_engine_contract.py::test_transcribe_refuses_to_leave_its_directory` | A real engine refuses a path escape. A stand-in that allows it passes a caller's traversal defect straight through to the real engine. |
| the engine stops saying what it is | `tests/test_engine_contract.py::test_it_says_what_it_is` | A caller could not tell the stand-in from a real engine somebody left running on the same workstation. |
| the client ignores its contract and hardcodes a path | `tests/test_contract.py::test_the_wire_used_this_contract_s_paths` | The whole agnosticism claim. A default that is the only value ever honoured is a hardcoding with a longer spelling, and everything still passes against the default engine. |
| the client ignores its contract and hardcodes a response key | `tests/test_contract.py::test_the_loop_closes_on_either_contract` | Same claim from the other side: reading a fixed key works against every engine that happens to spell it that way, and no others. |
| the engine answers a fixed path rather than its contract's | `tests/test_contract.py::test_the_loop_closes_on_either_contract` | The stand-in would only ever stand in for one engine, so a test claiming to exercise another would be exercising nothing. |
| the loop stops going over the wire | `walkthrough/test_closed_loop.py::test_it_went_over_the_wire` | `closed` would be True by construction. The loop would report success without ever reading back what it wrote. |
| speaking stops writing a file | `walkthrough/test_closed_loop.py::test_the_audio_is_a_real_file_written_this_run` | With a leftover file from a previous run the loop still closes. This is the stale-artifact hole, and it is why the walkthrough uses a fresh directory. |
| the synthesizer stops being deterministic | `tests/test_synth.py::test_the_same_text_gives_the_same_bytes` | An unseeded rng puts a clock in the fricatives, so the same words produce a different file every run and any recorded artifact churns. |
| the synthesizer ignores its input | `tests/test_synth.py::test_different_text_gives_different_bytes` | One sound for every phrase. The determinism test passes perfectly against it, which is why that test is not enough on its own. |
| every call builds its own client again | `tests/test_stt.py::test_one_client_serves_every_call` | Nothing breaks — the loop just costs ~620 ms per call again. A regression with no symptom except slowness is the kind nobody notices. |
