# ⚗️ 科研配方量纲校验台（dimcheck）

录入**带单位的计算表达式**与变量取值，系统解析括号、幂运算与函数调用，**递归推导量纲**并自动完成单位换算；对**相加异量纲**、**非法幂指数**、**未定义变量**等错误，给出**指向表达式具体片段**的定位信息，并展示 SI 换算结果。可为直接测量输入填写**带单位的标准不确定度**，系统以各独立输入为扰动源做双侧小步长重算，按**一阶平方和（RSS）**给出结果的**合成标准不确定度**，标称值与不确定度随目标单位一起换算。支持校验案例的**保存 / 加载 / 删除**与 **JSON 导出**。

技术栈：Python 3.11+ · FastAPI · SQLite · 原生 HTML/JS/CSS（无前端构建）。

---

## 快速开始

### 方式一：Docker Compose（推荐，一键启动）

```bash
docker compose up --build
```

启动后访问（浏览器直接打开即可，无需额外配置）：

- Web 界面：<http://localhost:8000/>
- API 文档（Swagger UI）：<http://localhost:8000/docs>
- OpenAPI 描述：<http://localhost:8000/openapi.json>
- 健康检查：<http://localhost:8000/api/health>

服务固定监听容器内 **8000 端口**，并映射到宿主机 **8000 端口**（Web 页面与 JSON API 同一地址、同一端口）；SQLite 数据存储在命名卷 `dimcheck-data` 中，容器重建不丢失。修改宿主机端口可改 `docker-compose.yml` 中的 `"宿主端口:8000"`。

### 方式二：本地运行

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

随后同样访问 <http://localhost:8000/>。仅需 Python 3.11+，无其他系统依赖；可选环境变量见文末。

### 运行测试

```bash
pip install -r requirements-dev.txt
pytest tests/ -q
```

---

## 表达式语法

| 特性 | 示例 |
| --- | --- |
| 四则运算 / 括号 | `(1 + 2) * 3` |
| 幂运算（右结合，也可用 `**`） | `v^2`、`2^3^2 = 512`、`m^-2` |
| 数字带单位 | `9.8 m/s^2`、`1.5e-3 mol`、`2.5 kJ` |
| 隐式乘法 | `2 m`、`kg m/s^2`、`2(3+4)`、`2π r` |
| Unicode 上标 | `m²`、`x⁻¹`、`5 m³` |
| 符号别名 | `× ÷ · −`（等价于 `* / * -`） |
| 函数 | `sqrt(4 m^2)`、`sin(30 deg)`、`ln(e)`、`log(100)`、`abs(-3 m)` |
| 变量 | 变量值本身是表达式，可相互引用：`F = mass * g0` |
| 内置常量 | `pi π e c g0 G kb NA R qe me eps0 mu0` |

**名称解析顺序**：用户变量 > 内置常量 > 内置单位。变量名与单位/常量同名时按变量解析，并返回非致命**警告**（`warnings` 字段），避免静默错误。

**量纲规则**：

- 加 / 减：两边量纲必须一致，否则报 `dim_mismatch`，错误片段指向右操作数；
- 乘 / 除：量纲指数相应加减，数值自动换算到 SI；
- 幂：指数必须**无量纲**（否则 `bad_exponent`，片段指向指数），底数量纲指数乘以指数（允许分数，如 `(4 m^2)^0.5 = 2 m`）；
- `sin / exp / ln` 等函数参数必须无量纲（否则 `dimless_arg`）；`sqrt` 使量纲指数减半；
- 未定义的名称报 `undefined`，片段指向该标识符；变量循环引用报 `var_cycle`。

完整单位列表见 Web 界面右侧「单位 · 常量 · 函数」面板或 `GET /api/units`。注意：摄氏温度 °C 等带偏移的单位不支持，请使用开尔文 K。

---

## 测量不确定度分析

在变量行的 **「u」列**为**部分直接测量输入**填写标准不确定度（带单位，如 `0.01 kg`）；不填的变量视为未提供。变量值仍可写成表达式并相互引用（如 `F = mass * g0`），但**只有定义式不引用其他用户变量的“直接输入”才能作为独立扰动源**——间接变量的不确定度由其依赖的输入经变量链传播，无需也不能单独标注。

后端对每个不确定度做校验：

- 必须是非负有限数值（`uncert_negative`）；
- 量纲必须与对应变量一致（`uncert_dim`），允许不同单位前缀（`2 cm` 对应 `m` 量变量）；
- 只能是“带单位的数值”，不能引用变量（`uncert_ref`），对应的变量必须存在（`uncert_unknown`）；
- 不能标注在间接变量上（`uncert_indirect`）。

计算方法（一阶 GUM 方式，数值实现）：

1. 以每个直接输入 xᵢ 为独立扰动源，步长 h = 10⁻⁶ · max(|xᵢ|, u(xᵢ))——**标称值为零时步长由其不确定度（再退化为绝对小量）托底，保证步长始终有效**；
2. 在 xᵢ ± h 两处对**完整变量链 + 主表达式整体重算**（中心差分），数值灵敏度 ∂f/∂xᵢ = (f(x+h) − f(x−h)) / (2h)；
3. 合成标准不确定度 u_c(y) = √( Σ (∂f/∂xᵢ · u(xᵢ))² )（各输入相互独立）；
4. 选择目标单位时，**标称值与合成标准不确定度按同一换算因子一起换算**，结果表并列显示。

**失败即停止汇总**：标称计算本身失败时不进行不确定度分析；任一输入校验失败、或某一侧扰动重算触发量纲错误 / 定义域错误（如零处 `sqrt` 的负向扰动）/ 非有限值时，响应 `ok=false`，错误中带 `perturbed`（扰动输入名）与 `direction`（`+` / `−`）定位来源，`result.uncertainty` 为 `null`——不会返回貌似有效的不确定度数值。标称值本身仍然正常返回。

请求示例：

```bash
curl -s http://localhost:8000/api/check -H 'Content-Type: application/json' -d '{
  "expression": "0.5*mass*v^2 + mass*g0*h0",
  "variables": {"mass": "2 kg", "v": "10 m/s", "h0": "5 m"},
  "uncertainties": {"mass": "0.01 kg", "v": "0.1 m/s", "h0": "0.02 m"},
  "target_unit": "kJ"
}'
```

响应 `result.uncertainty`：

```json
{
  "method": "central_difference_rss",
  "step_relative": 1e-6,
  "si_value": 2.2659724347122303,
  "si_unit": "m^2·kg·s^-2",
  "target": {"unit": "kJ", "value": 0.0022659724347122304},
  "sources": [
    {"name": "mass", "u_si": 0.01, "step_si": 2e-6,
     "sensitivity": 99.03325, "contribution_si": 0.9903325}
  ]
}
```

`sources` 给出每个扰动源的输入标准不确定度、实际步长、数值灵敏度与单项贡献（前端另显示方差占比）；不确定度为 0 的输入不列入贡献表。

---

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/check` | 校验表达式，返回量纲、SI 数值、目标单位换算、合成标准不确定度、错误/警告 |
| GET | `/api/units` | 单位 / 常量 / 函数目录 |
| POST | `/api/cases` | 保存校验案例（含 `uncertainties`） |
| GET | `/api/cases` | 案例列表 |
| GET | `/api/cases/{id}` | 读取案例 |
| DELETE | `/api/cases/{id}` | 删除案例 |
| GET | `/api/export` | 导出全部案例（JSON 附件下载） |
| GET | `/api/health` | 健康检查 |

`POST /api/check` 请求体字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `expression` | string | 主表达式 |
| `variables` | object | 变量名 → 带单位取值表达式 |
| `target_unit` | string \| null | 目标单位（标称值与不确定度一起换算） |
| `uncertainties` | object | 变量名 → 带单位的标准不确定度（仅直接输入，可只填部分变量） |

### 示例：校验通过

```bash
curl -s http://localhost:8000/api/check -H 'Content-Type: application/json' -d '{
  "expression": "0.5*mass*v^2 + mass*g0*h0",
  "variables": {"mass": "2 kg", "v": "10 m/s", "h0": "5 m"},
  "target_unit": "kJ"
}'
```

```json
{
  "ok": true,
  "errors": [],
  "warnings": [],
  "result": {
    "si_value": 198.0665,
    "dimension": ["2", "1", "-2", "0", "0", "0", "0"],
    "dimension_names": ["L", "M", "T", "I", "Θ", "N", "J"],
    "dimension_str": "L^2·M·T^-2",
    "si_unit": "m^2·kg·s^-2",
    "derived": ["J"],
    "target": {"unit": "kJ", "value": 0.1980665},
    "uncertainty": null
  }
}
```

未提供任何 `uncertainties` 时 `uncertainty` 为 `null`；提供时结构见上文「测量不确定度分析」。

### 示例：错误定位（相加异量纲）

```bash
curl -s http://localhost:8000/api/check -H 'Content-Type: application/json' -d '{
  "expression": "1 m + 1 s"
}'
```

```json
{
  "ok": false,
  "errors": [{
    "message": "量纲不一致：「L」与「T」不能相加",
    "start": 6, "end": 9,
    "kind": "dim_mismatch",
    "source": "expression"
  }],
  "result": null
}
```

`start` / `end` 是原始表达式字符串中的字符区间（上例即片段 `"1 s"`），`source` 取值：`expression`（主表达式）、`var:<名称>`（某变量的定义式）、`uncert:<名称>`（某变量的不确定度输入）、`target`（目标单位）。前端据此将出错片段高亮标红。

### 错误类型（`kind`）

| kind | 含义 |
| --- | --- |
| `syntax` | 语法错误（空表达式、括号不配对、多余内容等） |
| `undefined` | 未定义的变量或单位 |
| `dim_mismatch` | 加减法两边量纲不一致 |
| `bad_exponent` | 幂指数不是无量纲量 |
| `dimless_arg` | 函数参数要求无量纲 |
| `arity` | 函数参数个数错误 |
| `var_error` / `var_cycle` | 变量定义出错 / 循环引用 |
| `target_dim` | 目标单位与结果量纲不一致 |
| `value` | 除零、定义域、非有限值等数值错误 |
| `uncert_negative` | 标准不确定度为负数 |
| `uncert_dim` | 标准不确定度量纲与变量不一致 |
| `uncert_ref` | 不确定度表达式中引用了变量（只允许带单位数值） |
| `uncert_unknown` | 不确定度对应的变量不存在 |
| `uncert_indirect` | 对引用了其他变量的间接变量标注不确定度 |
| `uncert_eval` | 扰动重算后量纲改变或结果非有限 |

扰动类错误（`uncert_eval` 及扰动中暴露的 `value` 等）还带有 `perturbed`（扰动来源变量名）与 `direction`（`+` / `-`，表示正/负向步长）字段，其 `source` 指向真正出错的表达式（主表达式或 `var:<名称>`）；不确定度输入自身的错误 `source` 为 `uncert:<名称>`。

### 保存与导出案例

```bash
# 保存
curl -s -X POST http://localhost:8000/api/cases -H 'Content-Type: application/json' -d '{
  "name": "自由落体", "expression": "0.5*g0*t^2",
  "variables": {"t": "3 s"}, "uncertainties": {"t": "0.01 s"},
  "target_unit": "m"
}'

# 导出全部案例为 JSON 文件
curl -OJ http://localhost:8000/api/export
```

---

## 项目结构

```
├── app/
│   ├── main.py        # FastAPI 应用：API 路由 + 静态页面
│   ├── engine.py      # 引擎：词法分析 → 递归下降解析 → 量纲/数值求值、双侧扰动 RSS 不确定度
│   ├── units.py       # 单位 / 常量 / 函数注册表，量纲格式化
│   ├── db.py          # SQLite 案例存储（含 uncertainties 列，旧库自动迁移）
│   └── static/        # 原生前端（index.html / app.js / style.css）
├── tests/             # pytest：引擎单元测试（含 test_uncertainty.py）+ API 集成测试
├── Dockerfile
├── docker-compose.yml
├── requirements.txt       # 运行依赖（fastapi / uvicorn）
└── requirements-dev.txt   # 测试依赖（pytest / httpx）
```

## 配置与环境变量

无需任何必填配置：默认即在 `0.0.0.0:8000` 提供 Web 页面与 API（同一端口）。Docker Compose 一键构建启动并将宿主机 `8000` 端口映射到容器 `8000`。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DB_PATH` | `./data/app.db`（容器内 `/app/data/app.db`） | SQLite 数据库文件路径；父目录不存在时自动创建 |

如需更换端口：本地运行改 `uvicorn ... --port <端口>`；Docker 改 `docker-compose.yml` 的端口映射（容器内仍监听 8000，`Dockerfile` 的 `EXPOSE` 与健康检查无需改动）。
