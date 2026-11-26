# 交付相关文档（Word）

本目录存放结项移交用 **Word 版**文档，由同级 Markdown 源文生成。

| Word | 对应 Markdown 源 |
|------|------------------|
| [整体部署运维手册.docx](./整体部署运维手册.docx) | `../整体部署运维手册.md` |
| [源码移交说明.docx](./源码移交说明.docx) | `../源码移交说明.md` |

**格式约定：** A4、页眉题名、页脚页码、三级目录、标题用微软雅黑、正文宋体 12pt 1.5 倍行距、表格宋体 10.5pt。

重新生成（需本机已安装 `pandoc` 与 `python-docx`）：

```bash
python deploy-docs/交付相关文档/_md_to_docx.py
```

`_reference_styles.docx`、`_md_to_docx.py` 为生成辅助文件，不必随业务交付包对外分发。
