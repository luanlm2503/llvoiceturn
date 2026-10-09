# SDD ledger — plan: docs/superpowers/plans/2026-09-30-voxcpm2-voice-cloning.md

Execution mode: inline/native on main per prior user choice; do not commit.
Pre-existing workspace changes: VoxCPM base TTS files/docs uncommitted and deploy/docker-compose.yml modified; preserve all.
Plan/spec read: clone WAV/MP3 no transcript, loopback only, 25 MiB / 30s limits, librosa existing dependency, temporary-file cleanup.
Pre-flight interfaces: Task 1 adds multipart `/clone` and model's optional `reference_wav_path`; Task 2 client `clone(text, reference_audio: Path, style)` consumes exact multipart route; Task 3 calls client clone only if reference selected. Consistent.
Task 1: complete — sidecar API suite 12/12 pass (RED observed as five 404 route failures). Task 2: complete — HTTP clone tests 4/4 pass (RED missing clone method). Task 3: complete — UI clone tests 5/5 pass (RED absent chooser and clone routing). Task 4 automated: sidecar 12/12, client 22/22, server 2/2. Manual real RTX 3060 test remains user-run and is not complete.
Rulings: none.
Final review: self-review (no subagent tool used). The user instructed no commit; existing unrelated Compose change preserved.
