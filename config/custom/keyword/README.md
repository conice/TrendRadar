# 自定义关键词配置

这里存放带时效性或个人偏好的完整词表，例如 `tech.txt`、`finance.txt` 或
`watchlist-2026-q3.txt`。在 `config/timeline.yaml` 中通过 `frequency_file` 选择。

注意：自定义文件会替代默认 `config/frequency_words.txt`，不会自动合并，因此应包含
完整的 `[GLOBAL_FILTER]` 和 `[WORD_GROUPS]` 内容。

建议在文件头记录：

```text
# Owner: your-name
# Last-Reviewed: 2026-09-05
# Expires: 2026-12-31
```

时效组应设置较小的数量上限，并在到期后删除或续期：

```text
[季度关注]
/(请替换为具体人物|作品|赛事)/ => 临时关注
@5
```

修改后运行：

```sh
python scripts/validate_frequency_words.py config/custom/keyword/your-file.txt
python scripts/audit_frequency_words.py --frequency-file config/custom/keyword/your-file.txt
```
