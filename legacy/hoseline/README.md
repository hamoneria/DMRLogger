# Legacy HoseLine prototype

These scripts use the BrandMeister HoseLine WebSocket/spotter path. HoseLine
provides already-decoded PCMU/G.711 audio, unlike the direct HBP path which
records DMRD/AMBE and requires AMBE decoding.

The main project flow is now the direct HBP recorder in the repository root.
Keep these scripts for reference and debugging only.
