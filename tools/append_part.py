from pathlib import Path

block = '''
    # --- Stage 3: Build batches ---
    log_fn("
=== 第3步: 构建翻译批次 ===")
    effective_max_keys = cfg.ai.max_keys_per_call
    batcher = Batcher(max_batch_keys=effective_max_keys)
    log_fn(f"每批最多 {effective_max_keys} 条")
    batches = batcher.group(all_mod_assets, key_filter=tm_miss_keys)
    log_fn(f"共 {len(batches)} 个批次, {sum(b.total_keys for b in batches)} 条待翻译")

    # --- Stage 4: AI Translation ---
    log_fn(f"
=== 第4步: AI 翻译 ===
模型: {cfg.ai.model}  |  API: {cfg.ai.api_base}")
    from .translator.ai_client import AIClient
    total_batches = len(batches)
    all_translations = dict(tm_hits)
    tm_new_entries: dict[str, str] = {}
'''

Path(r"D:Ö85\code\MCModTrans\modtrans\pipeline.py").open("a", encoding="utf-8").write(block)
print("written")
