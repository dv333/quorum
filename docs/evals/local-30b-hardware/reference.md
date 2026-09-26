# Running a 30B model locally: 48 GB Mac or 24 GB NVIDIA PC

*Reference answer written independently with web research, sealed in git before Quorum answered.*

> Best way to run a 30B-parameter model locally: 48 GB Mac or a PC with a 24 GB NVIDIA GPU? Speed, cost, quantization.

**Bottom line: If you'll run one 30B model at 4-bit and care most about speed per dollar, a PC with a used 24 GB
RTX 3090 is the better buy: roughly 2–3× faster than a 48 GB Mac mini for less money. Choose the 48 GB Mac if you
want higher-quality quantization (6- or 8-bit), long context, or a quiet low-power box, and accept slower replies.**

## Key points

- **Memory decides what fits.** A dense 30–32B model needs about 18–20 GB at 4-bit (Q4_K_M), about 25–27 GB at
  6-bit and about 32–34 GB at 8-bit, plus memory for the context (KV cache: roughly 0.25 MB per token for a 32B model
  like Qwen's, so about 8 GB for 32K tokens at 16-bit). A 24 GB GPU fits 4-bit with roughly 12–16K tokens of context
  (more with an 8-bit KV cache); 6-bit is tight and 8-bit doesn't fit without spilling to system RAM, which is slow.
- **The Mac holds more, but not all 48 GB.** macOS lets the GPU use about 75% of unified memory by default (about
  36 GB on a 48 GB Mac); `sudo sysctl iogpu.wired_limit_mb=…` raises it until reboot. That's enough for 6-bit with
  long context, or 8-bit with a short one.
- **Speed follows memory bandwidth.** Generating tokens is bandwidth-bound: an RTX 3090 has 936 GB/s and a 4090
  1,008 GB/s, versus 273 GB/s on the M4 Pro and about 307 GB/s on the new M5 Pro Mac mini (546 GB/s on an M4 Max).
  Reported speeds for a dense 32B at 4-bit: roughly 30–45 tokens/s on a 4090, a bit less on a 3090, and about
  10–18 tokens/s on an M4 Pro. Reading a long prompt (prefill) is several times faster on NVIDIA, which matters for
  coding agents and long documents.
- **Mixture-of-experts 30B models change the math.** Models like Qwen3-30B-A3B activate only about 3B parameters per
  token, so both machines are fast (tens of tokens/s on the Mac, 100+ on a 4090); then the Mac's extra memory for
  better quantization and context counts for more.
- **Cost.** A used RTX 3090 sells for about $700–$1,000 and a whole PC around it for about $1,500–$2,200; a 4090
  costs about $2,000+ used, which rarely pays off for single-user inference. A 48 GB Mac mini (M4 Pro) cost about
  $1,800–$2,000; check the new M5 Pro's 48 GB price (Macworld lists only the 24 GB base, $1,699). The PC
  draws about 350 W under load (3090) versus well under 100 W for the Mac, and is louder.

## Where the evidence is uncertain

- Tokens/s figures vary a lot with the runtime (llama.cpp vs Ollama vs MLX vs vLLM), context length and quant; treat
  them as ranges. On Macs, MLX is often faster than llama.cpp.
- New Mac chips (M5 Pro, shipping September 2026) have limited independent LLM benchmarks so far.
- Used 3090 prices and condition vary; mining-era cards may need new thermal pads.

## What this means in practice

- Mostly chat or coding with one 30B model at 4-bit, and speed matters: build or buy a PC with a used RTX 3090.
- Want 8-bit quality, 32K+ context, several models loaded, or a silent desk machine: get the 48 GB Mac (the M5 Pro
  if the price is close), and prefer MoE models for speed.
- Want both speed and capacity: a 32 GB RTX 5090, or two 3090s, costs much more; weigh it against a cloud GPU for
  occasional heavy jobs.

## Sources

1. Macworld, 2026 Mac mini (M6 and M5 Pro) specs and prices, 307 GB/s M5 Pro bandwidth.
   https://www.macworld.com/article/2964754/2026-mac-mini-m5-pro-design-specs-release-date.html
2. InsiderLLM, Mac Mini M4 for local AI: configurations and tokens/s. https://insiderllm.com/guides/mac-mini-m4-local-ai/
3. Mustafa.net, LLM tokens/s benchmarks, RTX 4090 vs 3090 (llama.cpp, Q4_K_M).
   https://mustafa.net/llm-tokens-per-second-benchmarks/
4. Run AI Home, used RTX 3090 value in 2026. https://runaihome.com/blog/used-rtx-3090-ai-value-king-2026/
5. Contra Collective, how much unified memory macOS gives the GPU and raising the wired limit.
   https://contracollective.com/blog/mac-unified-memory-wired-limit-gpu-large-local-llm-2026
6. Markaicode, llama.cpp on RTX 4090 with a 35B-A3B MoE model.
   https://markaicode.com/benchmarks/llamacpp-llama-33-rtx-4090-throughput-benchmark/
