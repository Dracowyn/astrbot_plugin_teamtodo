# astrbot_plugin_teamtodo 设计

> 状态：设计已确认（2026-10-30），经过一轮 review，R1–R13 均已落实。M1–M3.2 已实现；各里程碑的细化决定见对应章节的“定稿”，M4 见第 8 节。
> 本文记录需求讨论中定下的全部决策。实现时如需偏离，先改这份文档再改代码。

## 1. 定位

面向单人和群聊的协作 TODO，作为 AstrBot 的独立插件发布（MIT 许可）。提供四种入口：

| 入口 | 使用者 | 能力 |
|---|---|---|
| 聊天命令 `/todo …` | 所有人 | 完整的任务操作，以及空间的结构性操作 |
| LLM 工具 | 所有人（通过对话） | 增、查、改任务，切换自己的当前空间；不能删除，也不能做结构性操作 |
| WebUI 插件管理页 | AstrBot 管理员 | 管理所有空间、绑定、角色、分享链接、操作日志 |
| 公开看板 | 任何拿到链接的人 | 只读查看单个空间的看板 |

明确不做的：块编辑器、页面嵌套、自定义字段、自定义看板列、跨平台账号合并、每日汇总、重复任务、外部访客编辑。

## 2. 核心概念

### 2.1 空间（Space）

- 空间是任务的容器，与会话解耦：一个会话可以绑定多个空间，一个空间也可以被多个会话绑定（多对多）。
- 会话（群聊或私聊，以 `unified_msg_origin` 标识）第一次使用 todo 时，自动创建一个默认空间并绑定。私聊的默认空间就是个人空间。
- 自动创建的空间这样命名：
  - 群聊用群名；拿不到群名时用“群 <群号后 4 位>”
  - 私聊用“我的任务”
- 同一个会话绑定的空间之间不能重名：
  - 自动创建时撞名，自动加 `-2`、`-3` 后缀
  - 用 `join` 加入时撞名，直接拒绝，提示先把本会话的同名空间改名。空间只有一个全局名字，自动改名会让其他绑定会话看到的名字也跟着变
  - `space new` 撞名直接拒绝（名字是用户明确起的）
  - 改名时要检查该空间的所有绑定会话，在任一会话里撞名就拒绝
- 空间名不能含空白或 `#`，否则 `空间名#3`、`list 空间名` 这类写法无法解析：
  - 用户起的名字含这些字符时直接拒绝；
  - 自动命名时把群名里的这些字符去掉，“群 <后 4 位>”实际写作“群1234”。
- 空间最后一个绑定会话解绑后，空间不会被自动删除，在 WebUI 里显示为“未绑定”。

### 2.2 当前空间

- 当前空间按“人 × 会话”记录：每个人在每个会话里各有一个当前空间，用 `/todo use <空间名>` 切换，只影响自己。
- 没切换过的人，使用会话的默认空间。会话管理员（见第 2.4 节）可以用 `/todo default <空间名>` 修改会话的默认空间。
- 所有不指定空间的操作（命令和 LLM 工具）都落在说话人的当前空间。
- 回退规则：
  - 个人的当前空间已经和本会话解绑 → 回退到会话默认空间
  - 会话默认空间被解绑 → 改用剩下的绑定里最早的那个
  - 一个绑定都不剩 → 下次使用时自动再建一个

### 2.3 身份与成员

- 身份 = 平台 + 用户 ID。昵称只用于显示，每次出现时更新。不同平台上的同一个人视为不同成员。
- 成员是隐式的：在任一绑定会话里用过 todo（命令、LLM 工具、被 @ 指派）的人，自动成为该空间的成员。
- 只能指派给空间成员。在绑定会话里被 @ 的人当场成为成员，可以直接被指派，因为 @ 能证明他确实在这个会话里。只用昵称匹配时，先在已有成员里查找，群聊里查不到再查群成员列表（M3.1，见第 7 节末尾）。
- `/todo admin +@x` 同理：被 @ 的人不是成员时当场登记为成员再设为管理员；`-@x` 的对象不是成员仍然报错。

### 2.4 角色与权限

空间级角色分三种：所有者、管理员、成员。AstrBot 管理员在所有空间都拥有全部权限。

**所有者怎么产生**：
- 用命令显式创建的空间，所有者是创建人。
- 私聊自动建的个人空间，所有者是该用户。
- 群聊自动建的默认空间：触发者是群管理员时，所有者就是他；否则所有者暂时空缺，由 AstrBot 管理员，或第一个执行认领命令的群管理员认领。

| 操作 | 成员 | 负责人 | 空间管理员 | 所有者 |
|---|---|---|---|---|
| 创建任务 | ✓ | ✓ | ✓ | ✓ |
| 修改自己创建的任务 | ✓ | ✓ | ✓ | ✓ |
| 修改自己负责的任务、改其状态 | — | ✓ | ✓ | ✓ |
| 修改、删除别人的任务 | — | — | ✓ | ✓ |
| 生成或重置邀请码、开关公开链接 | — | — | ✓ | ✓ |
| 任命或撤销管理员 | — | — | — | ✓ |
| 删除空间（需二次确认） | — | — | — | ✓ |

**会话管理员**：会话层面的操作（`join`、`leave`、`default`、`claim`）不归空间角色管，由会话管理员执行。AstrBot 管理员始终算会话管理员。
- 群聊：群主和群管理员都算。数据来自 `event.get_group()` 返回的 `group_owner` 和 `group_admins`，按会话缓存 5 分钟。这两个字段要靠平台适配器补全，目前 aiocqhttp、Telegram、Discord 等实现了，其他平台只返回群 ID。拿不到的平台上，这些操作只有 AstrBot 管理员能做（README 写明）。
- 私聊：本人就是会话管理员。所以可以在私聊里 `join` 一个共享空间，在私聊里操作群项目。
- 普通成员不能改动会话的绑定关系。
- 除了用命令，AstrBot 管理员也能在 WebUI 里改绑定。

## 3. 任务模型

| 字段 | 说明 |
|---|---|
| 短编号 | 按空间独立递增，记作 `#N`；跨空间引用写 `空间名#N` |
| 标题 | 必填 |
| 状态 | 待办 / 进行中 / 已完成 / 已归档（已归档不显示在看板上） |
| 负责人 | 0 到多个空间成员 |
| 截止时间 | 可选，按配置时区存储 |
| 标签 | 0 到多个字符串 |
| 优先级 | 可选 |
| 描述 | 存 Markdown 原文。首版所有界面都按纯文本显示：保留换行，http/https 链接可以点击，不渲染 HTML |
| 创建人、创建会话 | 创建会话用于到期提醒 |
| 已提醒标记 | 到期提醒只发一次；修改截止时间后重置 |

- 一个任务只有一个状态。任何一个负责人、空间管理员或所有者都能把它标成已完成，标完整个任务就算完成。需要每人各做一份的事，拆成多个任务。
- 每个任务都有操作日志：谁、什么时候、从哪个会话、改了什么。日志在 WebUI 里可见，公开看板不显示。
  - M1 只记任务级操作。空间级操作（改名、邀请、任命管理员、绑定 / 解绑、删除空间）暂不记录：`events` 表以 `task_seq` 为键。M3 如果要在 WebUI 展示空间操作，需要新加一张表（走迁移 2）。
- 自动归档：任务完成满 N 天后自动转为“已归档”。N 可配置，默认 7，设为 0 表示关闭。
- 删除：
  - 任务是硬删除，但操作日志里会留一条删除记录，附带任务删除前的完整快照；
  - 删除空间时，其下的任务、日志、绑定、邀请码、分享 token 一并删除。

## 4. 聊天命令

所有命令以 `/todo` 开头；单独发 `/todo` 或 `/todo help` 显示帮助。`<ref>` 指任务引用：`#3`、`3`，或跨空间写成 `空间名#3`（只能引用本会话绑定的空间）。

**任务字段写法**（`add` 和 `edit` 通用，各字段顺序随意）：

| 写法 | 含义 |
|---|---|
| `@某人` | 负责人，可以 @ 多个 |
| 日期（见下文），后面可以跟 `HH:MM` | 截止时间；`无截止` 表示清除（仅 `edit`） |
| `!高` `!中` `!低` | 优先级；`!无` 表示清除（仅 `edit`） |
| `[标签]` | 添加标签；`[-标签]` 表示移除（仅 `edit`） |
| 其余文字 | 标题；用双引号括起来时按字面理解，里面的词不会被识别成字段 |

中文输入法打出的全角符号同样有效：`！高`、`【标签】`、`［标签］`、`＃3`，以及 `assign` 里的 `＋@a`、`－@b`。

**任务命令**：

| 命令 | 含义 | 权限 |
|---|---|---|
| `add <字段…>` | 在当前空间建任务 | 成员 |
| `list [空间名] [all]` | 列出任务；带 `all` 时包含已归档 | 成员 |
| `mine` | 列出指派给自己的待办和进行中任务。私聊里跨全部空间；群聊里只列本会话绑定的空间，免得把别处的任务标题发到群里 | 任何人 |
| `show <ref>` | 任务详情 | 成员 |
| `start` / `done` / `reopen` / `archive <ref>` | 改为进行中 / 已完成 / 待办 / 已归档 | 创建人、负责人、空间管理员 |
| `assign <ref> @a @b` | 整体替换负责人；只写 `<ref>` 表示清空 | 创建人、负责人、空间管理员 |
| `assign <ref> +@a -@b` | 增减负责人 | 同上 |
| `edit <ref> <字段…>` | 修改标题、截止时间、优先级、标签 | 创建人、负责人、空间管理员 |
| `desc <ref> <全文>` | 设置描述；不写全文表示清空 | 同上 |
| `delete <ref>` | 删除任务 | 创建人、空间管理员 |

**空间与会话命令**：

| 命令 | 含义 | 权限 |
|---|---|---|
| `spaces` | 列出本会话绑定的空间，标出默认空间和自己的当前空间 | 任何人 |
| `use <空间名>` | 切换自己在本会话的当前空间 | 任何人 |
| `default <空间名>` | 修改本会话的默认空间 | 会话管理员 |
| `space new <名字>` | 新建空间：绑定到本会话，执行人成为所有者，并切换为执行人的当前空间 | 会话管理员 |
| `space rename <新名字>` | 重命名当前空间 | 空间管理员 |
| `space delete <空间名> [确认码]` | 删除空间（二次确认） | 所有者 |
| `invite` | 为当前空间生成邀请码，旧码同时失效 | 空间管理员 |
| `join <邀请码>` | 把本会话绑定到邀请码对应的空间 | 会话管理员 |
| `leave <空间名>` | 把本会话从该空间解绑 | 会话管理员 |
| `claim` | 认领本会话绑定的无主空间（当前空间）；只能在群聊里执行，防止有人用邀请码把无主空间拉进自己的私聊后再认领 | 会话管理员 |
| `admin +@x` / `admin -@x` / `admin +123456` / `admin -123456` | 任命或撤销当前空间的管理员，可混用，一次最多 20 个对象（`MAX_ASSIGNEES`）；用户 ID 须为带正负号的 5–20 位 ASCII 数字（支持全角正负号，符号后可有空白）。先只读检查权限，非所有者一律得到同一句拒绝，不查成员、不拉群列表（拒绝优先于用法错误）。已有成员直接操作；新增 ID 不是成员时，须在本群成员列表中查到才登记（昵称取群名片），私聊或无法列出群成员时不登记，报“找不到 QQ 号 / 用户 ID …”（aiocqhttp 叫 QQ 号，其他平台叫用户 ID）；是否成员在改角色的同一事务里判定，以实际修改的空间为准；撤销非成员报错；任一对象出错则整条命令不生效。回复优先显示昵称，无昵称显示 ID | 所有者 |
| `share` / `share on` / `off` / `reset` | 查看、开启、关闭公开链接，或重置 token（M4，规则见第 8 节“M4 定稿”） | 空间管理员 |

上表的“空间管理员”同时包含所有者；AstrBot 管理员在所有空间都拥有全部权限。“成员”就是任何在绑定会话里用过 todo 的人，第一次使用时自动加入。

**实现说明**：
- `/todo` 注册为单个命令，在 `main.py` 里按第一个词分发，没有用 `command_group`。原因是 AstrBot 收到不带子命令的指令组时，会直接回复它自己的“参数不足”和指令树，插件没法改成显示自己的帮助。代价是 WebUI 的指令管理里只能看到 `todo`。
- `edit` 里写 `@某人` 会被拒绝，提示改用 `assign`，免得说不清是替换负责人还是追加。

**输入长度上限**（命令、LLM、WebUI 三个入口统一执行，常量定义在 `core/models.py`）：

| 项 | 上限 |
|---|---|
| 标题 | 200 字 |
| 描述 | 4000 字 |
| 单个标签 | 20 字 |
| 标签数 | 10 个 |
| 负责人数 | 20 人 |
| 空间名 | 30 字 |

**邀请码**：
- 12 位随机码，默认 7 天有效，有效期内可以多次使用；
- 重置后旧码立即失效；
- `join` 失败按会话限流，每小时最多 5 次。

**二次确认**：删除空间等危险命令第一次执行时，只返回一个 4 位确认码。60 秒内带上这个确认码再执行一次同样的命令，才真正生效。确认码输错一次就作废，需要重新发起命令拿新码。

**日期格式**：命令里只认确定格式，包括 `2026-11-05`、`11-05`、`11-05 18:00`、`今天`、`明天`、`后天`、`周一`到`周日`（指下一个周一到周日）。只写日期时，默认当天 23:59 截止。自然语言日期交给 LLM 换算成 ISO 时间。

## 5. LLM 工具

- 以说话人的身份和权限执行，规则与第 2.4 节完全相同。
- 每个工具都有可选参数 `space`（空间名），不填时取说话人的当前空间。只能操作本会话绑定的空间；`mine` 例外，它只读取指派给说话人本人的任务。
- 工具范围：
  - 创建任务
  - 查询任务（当前空间，或指派给我的）
  - 修改任务（状态、负责人、截止时间、标签、优先级、描述）
  - 列出本会话绑定的空间
  - 切换自己的当前空间
  - 由空间管理员从当前群同步成员（仅在用户明确要求同步或导入群成员时调用）
- 不提供删除任务，也不提供建空间、绑定、解绑、删空间、邀请码、分享链接这类操作。唯一涉及成员结构的例外是 `teamtodo_sync_members`：只允许空间所有者、空间管理员或 AstrBot 管理员在用户明确要求时从当前群补全成员；它不是按昵称指派失败的恢复手段（指派本身已按群名片和账号昵称查本群，同步只存一个显示名，修不好查不到的名字，只会多出一次整群写入），查不到人时应让用户 @ 对方。不提供角色修改或成员删除工具。
- 截止时间参数用 ISO 8601。“下周三下午”这类自然语言由 LLM 自己换算。
- 指派负责人时，先读当前消息事件里的 @ 对象（LLM 看不到用户 ID）。没有 @ 时才按昵称在空间成员里匹配；匹配到多个人就把候选人返回给 LLM，让它反问用户，不能自己猜。
- 说话人身份只从事件里取，绝不作为工具参数交给 LLM 填写。
- 工具名统一加 `teamtodo_` 前缀，因为 AstrBot 的工具名是全局命名空间，同名会直接替换。
- 工具执行时先检查本会话是否启用了本插件，因为“按会话禁用插件”只过滤事件处理器，管不到 LLM 工具。
- 返回给 LLM 的任务标题、描述等用户写入的内容，要用每次随机生成的边界标记包裹，防存储型提示注入。最坏情况下的后果也受说话人权限限制。

**M2 定稿**（`services/llm_tools.py`）：

| 工具 | 作用 | 写操作 |
|---|---|---|
| `teamtodo_add_task` | 建任务：`title` 必填；可选 `assignees`、`due`、`priority`、`tags`、`description`、`space` | 是 |
| `teamtodo_list_tasks` | `scope=space`（默认）列空间任务，可带 `include_archived`；`scope=mine` 列指派给说话人的任务，范围规则与 `/todo mine` 相同 | 否 |
| `teamtodo_get_task` | 按编号看任务详情 | 否 |
| `teamtodo_update_task` | 按编号改状态、标题、截止、优先级、标签增删、描述、负责人增删；一次调用的全部改动在同一个事务里，任何一项失败就全部不生效 | 是 |
| `teamtodo_list_spaces` | 列本会话绑定的空间，标出默认和当前空间 | 否 |
| `teamtodo_use_space` | 切换说话人在本会话的当前空间 | 是 |
| `teamtodo_sync_members` | 从当前群同步成员到本会话绑定的空间；可选 `space`，不填取当前空间；仅空间所有者、空间管理员或 AstrBot 管理员可用；工具描述要求只在用户明确要求同步或导入群成员时调用，不得用于恢复查不到的名字 | 是 |

- 写操作只在说话人主动唤醒机器人的那一轮执行（`event.is_at_or_wake_command`）。机器人主动插话的轮次里，LLM 读到的上下文可能来自别人的消息，这时写工具一律拒绝；读工具不受限。
- 负责人参数是显示名列表，每个名字按以下顺序解析：
  1. 本条消息里 @ 的人，按 @ 显示名匹配（忽略大小写和开头的 `@`）；
  2. 目标空间成员，按昵称完全匹配（忽略大小写）；
  3. 前两步都未匹配，且是群聊、目标空间绑定在本会话时，按需获取本群成员列表；显式传入 `space` 也适用。按群名片或账号昵称完全匹配（去除首尾空白和开头的 `@`，忽略大小写）；群成员列表缓存在消息事件上（`event.set_extra`，获取失败的 `None` 也缓存），同一条消息触发的所有工具调用（含 `teamtodo_sync_members`）最多获取一次。第一次查空间成员时就把目标空间钉成具体的空间名（省略 `space` 时即当时的当前空间），之后的成员查找和最终写入都用这个名字，获取期间 `/todo use` 或 `/todo default` 改了当前空间也不会写到别的空间；钉住的空间在写入前被解绑或删除时报“本会话没有绑定名为…的空间”。获取（await）之后重新取服务，插件已停止时报未就绪，不写入。机器人账号 ID 为空时不获取（无法排除机器人自己）。私聊不拉取群成员。平台不支持或获取失败时沿用原来的无匹配结果。匹配到的人随指派登记为空间成员；已有空间成员优先；
  4. 匹配到多个不同用户：返回“有 N 位成员叫 X，请让用户直接 @ 对方”；一个都没有：返回“空间成员里没有 X，请让用户 @ 对方”。不做模糊匹配，不自己猜。
- `due` 传 ISO 8601；只有日期时按 23:59；没有时区时按配置时区。空字符串表示清除截止时间（仅 update）。`priority` 取 `high` / `medium` / `low`，update 里 `none` 表示清除。
- 返回格式：成功时一行英文说明，加上用随机边界标记包裹的 JSON 数据（任务编号、空间名、标题、状态、负责人昵称、截止时间的本地 ISO 字符串、是否逾期、标签、优先级、描述），另附当前时间，方便 LLM 换算相对日期。不返回任何用户 ID。失败时返回 `Error: <原因>`。
- 工具新增负责人时，同样按第 6 节发指派通知。
- `teamtodo_sync_members` 只同步当前群，不遍历空间的其他绑定会话；整群列表支持范围是 QQ（`aiocqhttp`），其他平台只能使用事件提供的成员列表。先只读解析已绑定目标空间并校验管理员权限，再校验群聊，均通过后才拉取成员；没有已绑定目标空间时直接拒绝，不自动创建空间，AstrBot 管理员也不例外。异步拉取后在最终事务里重新解析目标空间并重新检查绑定和权限，避免等待期间切换空间或权限变动导致误写。仅登记与当前会话同平台的人：已有成员刷新昵称但保留角色，不移除未出现在列表里的人。以聊天说话人和当前会话记录 `SYNC {session, added}` 日志；返回随机边界包裹的 `{space, added, total}`，其中 `space` 是空间名，新增人数与空间成员总人数是仅有的成员信息，不返回成员名单或用户 ID。

## 6. 通知

- **指派通知**：任务新增负责人时，在当前会话 @ 新加入的负责人。其他变更不主动发消息。
- **到期提醒**：在截止时间前一段时间提醒（提前量可配置），每个任务只提醒一次，消息里 @ 全部负责人。按以下顺序选目标会话：
  1. 创建任务的会话，前提是它仍然绑定着该空间；
  2. 否则，发到该空间现存绑定里最早绑定的那个会话；
  3. 一个绑定都没有就不发，只在 `/todo list`、`/todo mine` 和看板上把任务标成“已逾期”。
- 已完成和已归档的任务不提醒。
- 插件重启后：过去 24 小时内到期、还没提醒过的任务补发提醒；更早的直接标记为“已提醒”，不再补发，免得集中刷屏。
- 不做私聊回退。原因有两个：
  - `context.send_message` 的返回值只表示“找到了平台”，不表示送达，插件无法判断私聊有没有发成功；
  - QQ 不能给非好友主动发私聊，`qq_official` 完全不支持主动发送。

**M2 定稿**：
- 指派通知：
  - 命令入口把 @ 直接接在回复后面：先是原回复文本，换行后逐个 @ 新负责人，最后是“你被指派了这个任务”。LLM 入口另发一条同样格式的消息。
  - 只在群聊里 @；私聊里没有其他人可 @，不发。
  - 执行人把自己设为负责人时不 @ 自己。
- 到期提醒（`services/reminders.py`）：
  - 后台循环每 60 秒检查一次，插件 `initialize` 时启动，`terminate` 时取消。第一轮在启动 120 秒后才跑：AstrBot 先初始化插件、后初始化平台适配器，立刻跑的话，补发的提醒会在平台连上之前被标记并发丢。
  - 提醒和指派通知都只 @ 与目标会话同平台的负责人：共享空间可能同时绑定 QQ 群和 Telegram 群，别的平台的 ID 在这里没有意义，还可能碰巧对应到一个无关的人。
  - LLM 工具拒绝定时任务（cron）事件：这类事件的发送者是合成的，群聊里是群号。
  - 先标记“已提醒”再发送，保证最多提醒一次。发送失败只记日志，不重试。
  - 补发规则不区分是否刚重启，每轮统一按下面处理：截止时间早于“现在 − 24 小时”且未提醒的，静默标记为已提醒；其余“截止时间 − 提前量 ≤ 现在”的，正常提醒。
  - 目标会话找不到（没有绑定），或者目标会话里本插件被禁用，都只标记、不发送。
  - 提醒消息：群聊里先逐个 @ 负责人，再接正文；私聊或没有负责人时只有正文。正文写明空间名、`#编号`、标题，以及“将于 MM-DD HH:MM 到期”或“已于 MM-DD HH:MM 逾期”。
- 自动归档在同一个循环里做：已完成且 `done_at` 早于“现在 − N 天”的任务改为已归档，`done_at` 不变，日志的操作人为空、`detail` 里带 `"auto": true`。

## 7. WebUI 插件管理页

- 通过 AstrBot 插件页面机制接入，接口用 `context.register_web_api` 注册在 `/api/plug/…` 下，复用 WebUI 的登录鉴权。
- 能力：
  - 浏览所有空间（包括未绑定的）
  - 在看板上拖拽改状态，编辑、删除任务
  - 管理会话绑定、角色、邀请码、分享链接
  - 查看操作日志

**M3 定稿**：

- 决定（用户选定）：
  - WebUI 建的任务，创建人记为 `UserRef("webui", 登录用户名)`；WebUI 的写操作在日志里记 `actor_label=用户名`，`actor` 为空。
  - 会话显示标题单独存一张表 `session_titles`。每次有人在该会话用 `/todo` 或 LLM 工具时刷新：群聊存群名，私聊存发送者昵称。解绑后标题仍保留。
  - 新增空间级日志表 `space_events`（迁移 2）。聊天命令和 WebUI 的空间级操作都写进去（动作和 detail 格式见 `core/models.py` 的 `SpaceEventAction`），日志页和任务日志合并显示。
- 模块：
  - `services/admin_service.py`：WebUI 的业务操作。调用者都是 AstrBot 管理员，不做空间级权限检查，但长度上限和状态规则与聊天入口共用同一套代码。
  - `services/web_api.py`：路由、参数校验、序列化。路由注册为 `/astrbot_plugin_teamtodo/<path>`。
- 分享链接（`share`）属于 M4，M3 不注册这个接口，页面隐藏分享区域。M4 的接口和字段见第 8 节“M4 定稿”。
- 删除空间在 WebUI 里不用确认码，由页面弹窗二次确认。

**HTTP 接口**（前端用 `window.AstrBotPluginView.apiGet/apiPost` 调用；时间都是 Unix 秒；出错时用 `error_response(中文文案, status_code)` 返回：参数不对 400、空间或任务不存在 404、业务冲突 409，其余由 `TodoError` 转为 400）：

| 方法 | 路径 | 入参 | 返回 |
|---|---|---|---|
| GET | `meta` | | `{timezone, server_time, auto_archive_days, remind_before_minutes, limits:{title,description,tag,tags,assignees,space_name}}` |
| GET | `spaces` | | `{spaces: SpaceSummary[]}` |
| GET | `spaces/<space_id>` | | `SpaceDetail` |
| POST | `spaces/<space_id>/rename` | `{name}` | `SpaceDetail` |
| POST | `spaces/<space_id>/delete` | `{}` | `{ok: true}` |
| POST | `spaces/<space_id>/unbind` | `{session}` | `SpaceDetail` |
| POST | `spaces/<space_id>/members/role` | `{platform_id, user_id, role: "admin"\|"member"}` | `SpaceDetail` |
| POST | `spaces/<space_id>/members/sync` | `{}` | `{added, results: [{session, title, added, error}], detail: SpaceDetail}` |
| POST | `spaces/<space_id>/invite/reset` | `{}` | `{code, expires_at}`（明文码只在这次返回） |
| GET | `spaces/<space_id>/tasks` | `include_archived=1`（可选） | `{tasks: Task[], server_time}` |
| POST | `spaces/<space_id>/tasks` | `{title, assignees?: Ref[], due_at?, tags?, priority?, description?}` | `Task` |
| GET | `spaces/<space_id>/tasks/<seq>` | | `{task: Task, events: Event[]}`（新的在前） |
| POST | `spaces/<space_id>/tasks/<seq>` | 部分字段：`{title?, description?, status?, assignees?: Ref[], due_at?: number\|null, tags?, priority?: string\|null}`；`due_at`/`priority` 传 `null` 表示清除，`assignees`/`tags` 整体替换 | `Task` |
| POST | `spaces/<space_id>/tasks/<seq>/delete` | `{}` | `{ok: true}` |
| GET | `spaces/<space_id>/events` | `limit`（默认 50，最大 200） | `{events: Event[]}`（新的在前） |

数据形状：

- `Ref = {platform_id, user_id}`
- `User = Ref & {nickname}`：昵称取空间成员表，没有时用 `user_id`；WebUI 创建人的昵称是登录用户名。
- `Task = {space_id, seq, title, status, assignees: User[], due_at, tags, priority, description, creator: User, created_at, updated_at, done_at, overdue}`。不返回全局行 id、`created_session` 和 `reminded`。
- `Member = User & {role, joined_at}`
- `Binding = {session, title: string|null, bound_at}`
- `Event = {kind: "task"|"space", task_seq: number|null, action, summary, actor_name, created_at}`：`summary` 和 `actor_name` 由服务端用 `render.event_summary` / `event_actor_name` 生成，前端只负责显示，不解析 `detail`。
- `SpaceSummary = {id, name, owner: User|null, created_at, binding_count, member_count, task_counts:{todo,doing,done,archived}, overdue_count}`
- `SpaceDetail = SpaceSummary & {bindings: Binding[], members: Member[], invite: {expires_at}|null}`：邀请码明文不再返回。

**M3.1 群成员补全**（用户 2026-10-31 选定“两者都要”）：

问题：成员只在用过 todo 或被 @ 时登记，群里没互动过的人无法按昵称指派、无法设为管理员，WebUI 选人列表里也没有他们。

- 平台能力：只有 QQ（`aiocqhttp`）能列出整个群的成员，直接调 OneBot `get_group_member_list`，拿到群名片 `card` 和账号昵称 `nickname`。其他平台退回 `event.get_group().members`（多数为空或只有部分人）；Telegram 等列不出来的平台只能靠 @。
- 取成员：`services/group_members.py`。
  - `from_event(event)`：聊天入口用。不是群聊、平台不支持或调用失败时返回 `None`（失败记 warning），不打断原流程。
  - `from_session(context, session)`：WebUI 用。按会话串找平台实例；不是群聊或平台不支持时抛 `TodoError`，文案说明原因。
  - 两者都排除机器人自己；显示名取群名片，没有时取昵称；`GroupMember.names` 同时带上两者，供精确匹配。
- 聊天侧“用到时再查”：LLM 工具解析负责人时，@ 和已有成员都没匹配上，且是群聊、目标空间绑定在本会话时，拉一次本群成员列表；无论省略 `space` 还是显式指定本会话绑定的空间都适用，私聊不拉取。拉取前目标空间已钉成具体空间名，拉取后重新取服务。按任一名字精确匹配（去除首尾空白，忽略大小写和开头的 `@`）。规则与原来相同：多人同名报错让用户 @，不做模糊匹配。匹配到的人随指派当场登记为成员。同一条消息的所有工具调用共享一次拉取结果（缓存在事件上，失败也缓存），已有空间成员始终优先。
- WebUI 侧“从群同步成员”：设置页按钮，对空间绑定的每个群会话拉成员列表，全部登记为成员（`upsert_member`：已在的只刷新昵称，不改角色），不删除已退群的成员。每个会话写一条空间日志 `SYNC {"session", "added"}`。
  - `POST spaces/<space_id>/members/sync`，入参 `{}`，返回 `{added: number, results: [{session, title: string|null, added: number|null, error: string|null}], detail: SpaceDetail}`。某个会话失败只记在它的 `error` 里，不影响其他会话；空间没有绑定会话时 `results` 为空。
  - 每次拉群列表（await）之后都重新取服务；期间插件被停用或卸载时停止同步，返回与其他接口相同的 503。
- `/todo admin +<ID>`：只有通过权限预检、且有新增 ID 还不是当前空间成员时，才在群聊里拉一次群成员列表；`main.py` 只把在群列表里查到的 ID 及群名片交给 `set_admins`，“是否已是成员、能否登记”在 `set_admins` 的事务里判定。这样拉列表期间当前空间被 `/todo use` 或 `/todo default` 切换，也不会把空间 A 的成员身份带到空间 B 免验证登记。
- **M3.2 聊天侧主动同步**：`teamtodo_sync_members` 是唯一的管理员专用成员结构工具，只在用户明确要求同步或导入群成员时调用，不作为按昵称指派失败的恢复步骤（查不到人时让用户 @ 对方），支持显式指定本会话绑定的空间。只拉当前群一次，与同一条消息里的指派共享拉取结果，拉取后重新取服务；QQ 能取完整群列表，其他平台受事件成员列表能力限制。拉取前预检空间绑定及空间所有者、管理员或 AstrBot 管理员身份，最终事务重新解析空间并重验权限和绑定；保留已有角色，不移除缺席成员，跳过平台不符的人。日志保存聊天 actor、当前 session 及 `SYNC {session, added}`；LLM 只收到空间名、新增人数和总人数，不收到成员列表或用户 ID。WebUI 和聊天日志摘要均与 BIND/UNBIND 一样直接显示会话串，不把聊天同步误写成 WebUI 操作。

## 8. 公开看板

- 由插件自己启动的独立 HTTP 服务提供，不经过 WebUI 端口。原因是 `/api/plug/*` 和插件页面资源都需要登录 WebUI（见 AstrBot `astrbot/dashboard/server.py` 中的 `auth_middleware`）。
- 默认监听 `127.0.0.1`，端口可配置。分享链接用配置里的“对外 base URL”拼出来，方便套反向代理。需要直接暴露公网的用户，自行改成监听 `0.0.0.0`。
- 链接格式：`<base_url>/b/<token>`。token 随机生成，可以重置或撤销。token 无效时一律返回 404，不区分“空间不存在”和“token 错误”。
- 页面只读，显示标题、状态、负责人昵称、截止时间、标签、优先级、描述。不显示用户 ID 和操作日志。
- 页面每 30 秒轮询刷新，服务端按 IP 限流。
- 空间被删除后，它的公开链接立即失效。
- 安全约束：
  - token 至少 128 位熵；
  - 响应带 `Referrer-Policy: no-referrer` 和 `Cache-Control: no-store`；
  - 访问日志里的 token 要打码；
  - 公开接口按字段白名单输出，不直接序列化内部对象。
- 部署在反向代理后面时，服务端看到的都是代理的 IP。为此加一个“信任代理”配置项，默认关闭；开启后才从 `X-Forwarded-For` 取客户端 IP 来限流。
- 插件 `terminate()` 时必须关闭这个 HTTP 服务，否则热重载时端口会被占用。
- Docker 部署要在 compose 里多映射一个端口，README 写明。

**M4 定稿**（2026-10-31）：

- 存储：迁移 3 新增 `share_links(space_id PK → spaces ON DELETE CASCADE, token UNIQUE, created_at)`，每个空间最多一条，有这一行就表示已分享，关闭分享即删除这一行。token 明文存储，管理员可以随时再次复制链接；数据库本来就存着链接能看到的全部任务，哈希存储换不来额外的保护。模型 `ShareLink`，存储方法 `set_share` / `get_share` / `find_share` / `clear_share`。
- token：`secrets.token_urlsafe(24)`（192 位，32 个字符）。重置生成新 token，旧链接立即失效。
- 链接：`<对外访问地址>/b/<token>/`，结尾带斜杠，页面里的相对路径资源才能解析。对外访问地址去掉首尾空白和结尾的 `/`；不以 `http://` 或 `https://` 开头时视为未填写，并记 warning。
- 分享可用 = 配置里开启了公开服务，且对外访问地址有效。只看配置，不看端口是否绑定成功（绑定失败记 error 日志）。
- 聊天命令（全部要求空间管理员，与邀请码相同，`perms.can_manage_space`；作用于当前空间）：
  - `share`：已分享时回复链接，否则回复“当前空间没有开启公开链接”。
  - `share on`：分享不可用时报错“公开看板服务未开启或未配置对外访问地址，请联系 AstrBot 管理员”；已有链接时直接回复原链接，不换 token、不记日志；否则生成 token 并回复链接。
  - `share reset`：分享不可用时报同样的错；没有链接时报“当前空间还没有公开链接，用 /todo share on 开启”；否则换新 token 并回复新链接。
  - `share off`：删除链接（分享不可用时也能关）；没有链接时回复“当前空间没有开启公开链接”，不报错。
  - 实际发生变化时记空间日志 `SHARE {"op": "on"|"reset"|"off"}`，日志里不写 token。权限判断和写入在同一个事务里。
- LLM 不提供分享工具（第 5 节）。
- 公开数据（`GET /b/<token>/api/board`）只输出白名单字段：

  ```
  {space: {name}, tasks: PublicTask[], updated_at, server_time, timezone}
  PublicTask = {seq, title, status, assignees: [{nickname}], due_at, tags, priority, description, done_at, updated_at, overdue}
  ```

  任务只含待办、进行中、已完成，排序与管理页 `list_tasks` 相同。负责人昵称取空间成员表，查不到时显示“未知成员”，绝不回退到用户 ID。`updated_at` 取空间创建时间和所有可见任务 `updated_at` 的最大值。不含创建人、空间 ID、会话、日志、成员列表。
- 服务（`services/public_server.py`，aiohttp `AppRunner` + `TCPSite`，关闭自带访问日志）：
  - 路由只有 `GET`/`HEAD`：`/b/<token>` 相对重定向到 `<token>/`；`/b/<token>/` 返回 `public.html`；`/b/<token>/api/board` 返回数据；`/b/<token>/<路径>` 返回 `views/board` 下的 `.js` / `.css` 静态文件；其余一律 404。
  - 页面和静态文件不查 token：任何 token 都拿到同一个页面，链接是否有效只由数据接口回答，页面本身不能用来探测 token。数据接口对未知、已关闭、空间已删除的 token 返回同一个 404。
  - 限流只统计数据接口，按客户端 IP，默认每分钟 60 次（页面 30 秒刷新一次），超出返回 429。开启“信任反向代理”后取 `X-Forwarded-For` 最右边的一项（最近一层代理看到的地址），解析不出合法 IP 时退回连接对端地址。
  - 所有响应带 `Referrer-Policy: no-referrer`、`Cache-Control: no-store`、`X-Content-Type-Options: nosniff`；页面另带 CSP 和禁止被嵌入框架的头。日志里不出现 token。
  - 插件 `initialize` 时按配置启动，启动失败只记日志，插件其余功能照常；`terminate` 时先停服务再关数据库。数据回调每次请求现取服务，取不到时按 404 处理。
- WebUI：`meta` 增加 `share_available`；`SpaceDetail` 增加 `share: {url: string|null, created_at}|null`（对外访问地址无效时 `url` 为 null）；新接口 `POST spaces/<space_id>/share`，入参 `{action: "on"|"reset"|"off"}`，返回 `SpaceDetail`，规则与聊天命令相同，不可用或无链接可重置时返回 409，日志操作人记 WebUI 用户名。
- 公开页（`views/board/public.html` + `public-app.js`）：在 `/b/<token>/` 下用相对地址 `api/board` 取数据（`credentials: "omit"`），404 显示“链接无效或已关闭”并停止轮询，429 和网络错误按暂时失败处理。用本地静态服务器打开演示时（路径里没有 `/b/`）继续用 mock 数据，mock 模块按需动态加载，生产页面不加载。

## 9. 前端

- 不需要构建：使用原生 ES Modules，Vue 3（`vue.esm-browser.prod.js`）和 SortableJS 以单文件形式放在 `static/vendor/`。
- 这样做的原因：插件市场安装时下载的是仓库 HEAD 的源码压缩包，不会执行构建（见 AstrBot `astrbot/core/repository.py` 中的 `archive_url`）。源码就是运行时代码，下载即可运行，开源协作时 diff 也干净。
- 管理页和公开看板共用同一套看板组件，通过参数切换只读或可编辑。
- 插件页的 asset token 放在 URL 路径里（AstrBot `plugin_page_service.py` 的 `build_plugin_page_view_content_path`），所以相对路径的 ES Module `import` 会自动带上 token。但视图资源路径不允许出现 `../`，所以共享组件要放在 admin 视图目录里面，公开服务也从这个目录取文件。
- 视图标题写在 `.astrbot-plugin/i18n/<locale>.json` 的 `views.<name>.title` 里。插件元数据要声明 AstrBot 版本下限（插件页机制依赖的版本）。

## 9.1 实现约束

来自兄弟插件的经验库（Obsidian：`项目知识库/90-通用知识/AstrBot插件静默失效点`）：

- 指令和事件钩子的 handler 只写在 `main.py` 里，逻辑下沉到其他模块。
- 插件内部模块一律相对导入；第三方依赖在模块顶层导入。
- 导入自检要在 AstrBot 根目录执行 `__import__("data.plugins.<插件名>.main")`。
- LLM 工具用 `FunctionTool` 子类自己写 JSON Schema，不靠 `@filter.llm_tool` 解析 docstring。
- 分配短编号、判断权限再写入，都要在同一个数据库事务里完成，判断和写入之间不能有 `await`。

**模块划分（M1）**：

| 模块 | 职责 |
|---|---|
| `core/models.py` | 冻结数据类与长度上限，是全项目的契约 |
| `core/store.py` | SQLite 持久化（同步 `sqlite3`，WAL），不含权限逻辑 |
| `core/dates.py` | 解析命令里的截止日期 |
| `core/parsing.py` | 解析 `/todo` 命令参数（纯函数） |
| `core/permissions.py` | 权限判定（纯函数），实现第 2.4 节和第 4 节的权限表 |
| `services/access.py` | 判定会话管理员（带缓存）、生成和校验邀请码、限流、二次确认码 |
| `services/todo_service.py` | 业务编排：解析空间、隐式加入成员、做权限检查、写操作日志、执行长度上限 |
| `services/render.py` | 渲染聊天回复的纯文本 |
| `main.py` | 构造 Actor、注册 `/todo` 指令组、管理生命周期。指令 handler 只能写在这里 |
| `services/event_context.py`（M2） | 从事件取消息片段、构造 Actor；`main.py` 和 LLM 工具共用 |
| `services/llm_tools.py`（M2） | LLM 工具（`FunctionTool` 子类），只调 `TodoService`，不直接碰存储 |
| `services/reminders.py`（M2） | 到期提醒的选目标与标记、自动归档、后台循环；发送由 `main.py` 注入 |
| `services/task_ops.py`（M3） | `TodoService` 与 `AdminService` 共用的任务写入规则：长度校验、字段变更与状态规则、日志 |
| `services/admin_service.py`（M3） | WebUI 管理操作（调用者都是 AstrBot 管理员） |
| `services/web_api.py`（M3） | WebUI 接口：路由、参数校验、序列化 |
| `services/group_members.py`（M3.1） | 从平台拉群成员列表（聊天事件或会话串两种入口） |
| `services/share.py`（M4） | 分享 token 生成、链接拼接、开关重置的事务规则（聊天与 WebUI 共用）、公开数据白名单序列化 |
| `services/public_server.py`（M4） | 公开看板 HTTP 服务：路由、静态文件、限流、响应头 |

**验证口径**：
- 在插件目录下，用 AstrBot 的 `.venv` 运行：`..\..\..\.venv\Scripts\python.exe -m pytest -q --cov=astrbot_plugin_teamtodo --cov-report=term-missing tests`。`core/` 和 `services/` 每个模块的行覆盖率都要 ≥ 80%。
- 在 AstrBot 根目录执行 `uv run ruff check` 和 `uv run ruff format --check`，对象是插件目录，另外显式加上 `tests/*.py`：AstrBot 的 ruff 配置排除了 `tests` 目录，只按目录跑会漏掉测试文件。
- 导入自检：在 AstrBot 根目录执行 `__import__("data.plugins.astrbot_plugin_teamtodo.main")`。

## 10. 配置项

| 配置项 | 默认值 |
|---|---|
| 时区 | `Asia/Shanghai` |
| 到期提醒提前量（分钟） | 60（0 表示到点才提醒） |
| 完成后自动归档天数 | 7（0 表示关闭） |
| 公开服务开关（`public_enabled`） | 关闭 |
| 公开服务监听地址（`public_host`） | `127.0.0.1` |
| 公开服务端口（`public_port`） | 6200（AstrBot 自带适配器占用 6185、6194–6199） |
| 对外访问地址（`public_base_url`） | 空（不填就不生成分享链接） |
| 公开服务限流（`public_rate_limit`，每 IP 每分钟数据请求数） | 60；小于 1 时按 60 |
| 信任反向代理（`public_trust_proxy`） | 关闭 |

公开服务的配置在插件加载时读取，修改后需重载插件。端口不在 1–65535 时记 warning 并按 6200。

## 11. 存储

数据库为插件数据目录下的 SQLite。需要的实体如下，表结构在 M1 中确定：

- 空间
- 会话-空间绑定
- 成员及其角色
- 人 × 会话的当前空间
- 任务
- 任务负责人
- 任务标签
- 操作日志
- 邀请码
- 分享 token

## 12. 里程碑

每个里程碑都能独立运行，都带 pytest 测试。数据层和权限的测试覆盖率不低于 80%。

| 里程碑 | 内容 |
|---|---|
| M1 | 数据层（模型、空间、绑定、成员、权限）+ 聊天命令 |
| M2 | LLM 工具 + 指派通知 + 到期提醒 |
| M3 | WebUI 插件管理页 |
| M4 | 公开看板服务 |
