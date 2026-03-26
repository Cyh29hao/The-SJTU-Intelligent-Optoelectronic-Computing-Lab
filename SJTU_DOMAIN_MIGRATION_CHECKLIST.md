# SJTU 域名迁移准备清单

适用场景：当前网站先在本地 / GitHub 维护，后续迁移到 `xxxxxx.sjtu.edu.cn`。

## 1. 上线前必须确认

- 确认学校服务器申请已通过。
- 确认学校域名申请已通过，并拿到最终正式域名。
- 确认服务器运行环境：Python 版本、可用端口、是否支持 HTTPS 反向代理。
- 确认管理员账号、管理员邮箱、管理员密码策略。
- 确认是否需要校内备案、页面底部标识、学校统一页脚规范。

## 2. 代码与配置检查

- `IS_LOCAL` 在正式上线前改为 `0`。
- `debug=False`，不要在线上开启调试模式。
- 检查所有站内跳转是否都使用 Flask `url_for` 或相对路径。
- 检查下载链接、图片链接、logo 链接是否都能在新域名下正常访问。
- 检查管理员页面是否能正常上传论文、图片、站点素材。
- 检查 `secret_key.bin` 在线上是否存在且稳定，不要频繁重建。

## 3. 数据迁移策略

### 3.1 默认走 Git 的内容

- `render_data/site.json`
- `render_data/articles.json`
- `render_data/people.json`
- `render_data/images/`
- 模板、CSS、Python 代码

这些内容应当跟随 GitHub 仓库维护，正常 `git add` / `git commit` / `git push` 即可。

### 3.2 适合通过 sync 迁移的内容

- `render_data/data_logs/`
- `render_data/private_downloads/`
- 线上运行期间新增、且未及时提交到 Git 的素材

推荐做法：

- 日常页面与素材修改：优先走 Git。
- 上线前或线上线下快速对齐：使用后台 `render_data_bundle.zip` 做一次 sync。

## 4. 正式切换到学校服务器时的建议步骤

1. 本地整理好最终版本并提交到 GitHub。
2. 在本地后台下载一次最新 `render_data_bundle.zip` 作为备份。
3. 在学校服务器拉取最新 GitHub 代码。
4. 配置 Python 虚拟环境并安装依赖。
5. 放置线上 `secret_key.bin`。
6. 启动 Flask / WSGI 服务。
7. 通过后台上传一次本地备份的 `render_data_bundle.zip`，确保线上内容和本地一致。
8. 检查首页、文章页、成员页、后台、下载功能是否正常。
9. 再开启正式域名解析与 HTTPS。

## 5. 上线后优先检查

- 首页能否正常打开。
- 论文轮播是否正常。
- `People` / `Publications` / `Admin` 导航是否正常。
- 注册与下载功能是否正常。
- 页面浏览量和下载量是否继续正常累计。
- 站点 logo、SJTU、ICISEE、GitHub、邮箱图标是否正常显示。
- 管理员 `sync` 下载 / 上传是否正常。

## 6. 风险点

- 如果线上和本地同时修改了 `render_data`，但没有及时同步，可能出现内容覆盖。
- 如果 `secret_key.bin` 丢失，登录 session 会失效。
- 如果服务器路径或权限不对，图片上传和下载统计可能会异常。
- 如果学校服务器对外网访问有限，外链资源应尽量本地化。

## 7. 我建议的工作方式

- 日常开发：本地改代码和内容，提交 GitHub。
- 图标、logo、缩略图、成员照片：直接放进 `render_data/images/` 并提交 GitHub。
- 浏览量 / 下载量日志：保留在线上，不强制进 Git。
- 要做线上线下对齐时：只额外执行一次后台 sync。

## 8. 迁移完成的判定标准

满足下面几项，就可以认为迁移完成：

- 站点可通过 `xxxxxx.sjtu.edu.cn` 正常访问。
- 前台页面、后台页面、上传下载都正常。
- `render_data` 内容和本地一致。
- 统计数据持续可用。
- 后续维护时不需要反复手工搬运公开素材。
