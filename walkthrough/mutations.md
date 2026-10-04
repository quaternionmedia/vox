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
| the client drops the pause it was asked to send | `tests/test_stt.py::test_listen_sends_the_pause_when_the_contract_names_it_and_a_value_is_given` | A caller asking for a longer pause would get the engine's default and a sentence-length answer cut off mid-thought, with nothing failing. |
| the client sends the pause whether or not the contract names it | `tests/test_stt.py::test_listen_sends_no_pause_on_a_contract_without_one` | An engine with no such parameter would be sent one under a name it never agreed to, and the seam would have hardcoded a spelling again. |
| the client drops the hint it was asked to send | `tests/test_contract.py::test_a_hint_travels_in_this_contract_s_spelling_or_not_at_all` | A short answer would be transcribed with no idea of the words it should be, and nothing would fail. |
| the client sends the hint whether or not the contract names it | `tests/test_contract.py::test_a_hint_travels_in_this_contract_s_spelling_or_not_at_all` | An engine with no such parameter would be sent one under a name it never agreed to. |
| an announcement drops the question's options | `tests/test_stt.py::test_announce_carries_a_question_s_options_in_the_order_given` | A display could no longer offer the answers as controls, so a person who cannot be heard has no other way to answer. |
| the SAPI path plays with pyttsx3's say again | `tests/test_adapters.py::test_on_sapi_the_file_is_written_then_played_synchronously` | On the machine it was measured on, pyttsx3's say returned without playing: every sentence went into a file and the person heard nothing. |
| a named output that matches several is guessed | `tests/test_adapters.py::test_on_sapi_an_output_matching_several_or_none_is_refused` | A fragment naming several outputs would play to whichever came first, which can be a jack nobody hears. |
| a listen's confidence is read under a fixed name | `tests/test_contract.py::test_a_confidence_is_read_in_this_contract_s_spelling_or_not_at_all` | A caller deciding whether to ask for confirmation would read nothing from an engine that spells it differently, or something from one that never said. |
| the engine accepts a pause the engine it stands in for refuses | `tests/test_engine_contract.py::test_listen_refuses_a_silence_ms_joe_refuses` | A caller sending a pause outside the real engine's bounds would pass the offline suite and be answered 400 at the demo, which is the drift the stand-in exists to prevent. |
| the engine refuses the pauses at the edges of the bound it stands in for | `tests/test_engine_contract.py::test_listen_accepts_the_pauses_at_the_edges_of_joes_bound` | Every refusal case still passes against an exclusive bound; only a test sending the edges themselves tells an inclusive bound from one off by one. |
| every call builds its own client again | `tests/test_stt.py::test_one_client_serves_every_call` | Nothing breaks — the loop just costs ~620 ms per call again. A regression with no symptom except slowness is the kind nobody notices. |
