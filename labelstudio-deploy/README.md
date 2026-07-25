# LabelStudio 独立部署（仅标注，不进在线业务 compose）

## 快速启动

```bash
cd labelstudio-deploy
docker compose up -d
```

浏览器访问：http://localhost:8080

标注模板见：

- `../configs/llm_train/labelstudio/vl_ops.xml`
- `../configs/llm_train/labelstudio/defect_vl.xml`

导出 JSON 后放到 `../data/llm_train/raw/`，再用训练控制台或 `/train/llm/data/convert` 转换。
