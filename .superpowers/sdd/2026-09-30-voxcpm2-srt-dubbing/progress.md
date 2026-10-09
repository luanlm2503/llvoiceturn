# SDD ledger — plan: docs/superpowers/plans/2026-09-30-voxcpm2-srt-dubbing.md

Execution: Native/inline because user requested implementation directly; remain in current checkout to preserve uncommitted local TTS foundation. No commit. Spec: docs/superpowers/specs/2026-09-30-voxcpm2-srt-dubbing-design.md.
Pre-flight interfaces: Task 1 -> Task 5: `SrtCue`, `parse_srt`, `parse_srt_bytes`, `validate_cues`; UI displays and submits validated cue data. Task 2 -> Task 3: renderer destination/profile and overlong cue structured exception consumed by API failure schema. Task 3 -> Task 4: `/srt` route paths and status/result schema match client methods. Task 4 -> Task 5: `start_srt`, `srt_status`, `download_srt` signatures consumed by UI. No conflicts found against spec.
Ruling: Implementation method is Native/inline — user said “sửa code cho tôi luôn” after earlier explicit Native selection; cost if wrong: no per-task reviewer.
Task 1 complete: test-first SRT parser/validator; initial false out-of-order test fixture corrected; focused 12/12, client 55/55.
Task 2 complete: deterministic audio renderer, bounded 1.5x stretch, atomic WAV/MP3; test fixture corrected for target durations; focused 5/5, sidecar 27/27.
Task 3 complete: sidecar async SRT jobs and API; test-first 404s; found bad case generating later cue after first overlong and fixed by validating fit immediately; found/covered MP3 reference conversion; focused 7/7 API and full sidecar 34/34.
Task 4 complete: VoxCPM client; missing methods RED; corrected accidental static method; focused 3/3 and client 58/58.
Task 5 complete: SRT Qt page wired into only SRT tab; additional tests revealed invalid edits had no visible error and existing target remained enabled; fixed; focused/page tests 5/5 and client suite 63/63.
Task 6 automated verification: sidecar 34/34, client 63/63, server 2/2; diff-check clean; sidecar bound loopback. One failed combined shell due to wrong server cwd was rerun correctly. Manual GPU verification pending. No commit.
Safety follow-up TDD: added a renderer regression test for a time-stretch implementation returning a waveform longer than target; test passes only when renderer rejects rather than crops speech. Sidecar count now 35.
