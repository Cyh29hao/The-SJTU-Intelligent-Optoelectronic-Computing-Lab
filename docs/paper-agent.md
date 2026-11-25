# 论文草稿助手

## 一句话交给 Agent

> 请把我本机的《论文名.pdf》和补充材料《supplement.pdf》整理成论文草稿，上传到课题组本机预览站，给我预览链接和结果报告。原论文作为资料读取，补充 PDF 随草稿上传。

准备好完整管理员预览站，由站长在「脚本凭证」页为 Agent 提供 `OPTICOM_DRAFT_TOKEN`。在当前终端安全配置这个环境变量。手动使用脚本时也可以按提示粘贴凭证，输入会隐藏。遇到重名的论文或补充文件，请先确认具体路径。

## 执行

在项目目录运行，替换例子中的两个本机路径。

```powershell
python -X utf8 -m scripts.upload_paper_draft 'D:\素材\论文.pdf' --supplement 'D:\素材\补充材料.pdf' --site 'http://localhost:8000' --json
```

- 可重复使用 `--supplement` 添加多个 PDF。单个附件上限 20 MiB，本次附件合计上限 23 MiB。正文 PDF 默认只在本机读取。用户明确选作附件时再加 `--attach-paper`。
- 脚本优先读取本机 PDF 信息和文本。已收录论文可用项目索引补足字段。有明确 DOI 时，只把 DOI 用于 Crossref 登记信息查询。需要完全离线查询时加 `--offline`，仍可向 localhost 提交草稿。 [Crossref Works API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/)
- 添加 `--inspect` 只整理元数据，无需服务或凭证。报告列出待核对项。可整理为 UTF-8 JSON，用 `--metadata` 提供人工核对后的字段。常用字段为 `title`、有序的 `authors` 列表、`venue`、整数 `year`、`abstract`、可选 `abstract_zh`、`doi`、`paper_url`、`official_free_access_url` 和 `resource_url`。空缺信息保留待核对。
- 遇到请求超时或连接中断，使用同一批文件和相同参数重试。脚本会生成相同的提交编号。已有草稿会在回执中标明。新建和取回草稿都在编辑预览中核对。

## 给用户的报告

从 JSON 回执汇报 `preview_url`、草稿编号、创建或取回结果、源 PDF 路径、标题、作者顺序、期刊年份和 DOI。附件应按回执列出名称和大小。汇报 `warnings` 以及仍需人工核对的字段。用户可打开预览，登录后检查准备公开的附件，再决定是否发布。发布仍由内容编辑在后台操作。

平常提交全程可以用终端。需要向用户展示时再打开返回的 localhost 预览链接。
