# 薪资对比

控制台的「薪资对比」页可以并排查看已保存岗位的薪资信息。选择 2 至 6 个岗位，点击「查看对比」。页面保留公司、岗位、地点、招聘方的薪资原文和来源链接，并列出能够从原文明示条件中计算出的月薪与年额区间。

## 如何读取结果

| 招聘方薪资原文 | 页面结果 |
| --- | --- |
| `人民币20-30K元/月 · 13薪` | 月薪 20,000–30,000 元，按 13 薪折算年额 260,000–390,000 元 |
| `人民币20-30K元/月 · 14薪` | 月薪 20,000–30,000 元，按 14 薪折算年额 280,000–420,000 元 |
| `人民币2-3万元/月 · 12薪-13薪` | 月薪 20,000–30,000 元，按明示薪数区间折算年额 240,000–390,000 元 |
| `人民币20-30K元/月` | 月薪 20,000–30,000 元，发薪月数及年额待确认 |
| `20-30K` | 币种与计薪周期待确认，保留原文 |
| `20-30K/月` | 币种待确认，保留原文 |
| `年薪人民币30-50万元` | 标记为年薪，保留原文 |
| `USD20-30K/month` 或 `日元300000-500000/月` | 标记原币种与周期，保留原文 |
| 空白或 `面议` | 薪资待确认 |

折算要求原文同时明确人民币、月薪金额和发薪月数。金额可以使用元、千、K、万，并支持两端金额或固定金额。`13薪`、`14薪`、`12薪-13薪` 等明示薪数均可读取。年额下限使用月薪下限乘薪数下限，年额上限使用月薪上限乘薪数上限。

税前、税后只按原文显示，未说明时显示「待确认」。奖金、绩效、股权、税款和福利需要另行核对。`最高14薪`、`13薪以上` 等带条件的薪数会保留为待确认。包含多个金额构成、冲突的币种或计薪周期时，页面提示查看原文。

可以从原岗位或 HR 获取缺失信息，然后在岗位资料中按准确原文更新薪资字段。每项数字应能对应到招聘方的公开说明或已核实答复。例如招聘方只写 `20-30K` 时，先确认币种、按月还是按年、发薪月数和税前税后，再更新记录。

## 范围与记录

对比页使用已经保存在台账里的岗位数据。选择与查看通过本机 GET 请求完成，不修改岗位资料，也不触发投递或回复。岗位来源链接可供回查招聘方的实际条件。

当前对比支持明示人民币月薪的算术折算。年薪、时薪、日薪与外币金额按原文显示。各岗位按选择顺序展示，用户可结合地点、职责和其他条件判断。台账 CSV 继续保留薪资原文，便于自行整理。

## English

Open the salary comparison page and select 2–6 saved jobs. It shows each role's company, title, location, original salary text, currency, pay period, stated tax basis, and source link.

CNY monthly amounts can be normalized when the salary string states both the currency and the monthly period. Annual reference amounts additionally require explicit salary payment counts, such as `13薪` or `12薪-13薪`. Missing currency, pay period, payment count, or tax basis stays pending confirmation. The original text remains visible for annual, hourly, daily, and foreign-currency salaries.

The page reads local records and performs arithmetic. It preserves conditional pay information for review and leaves bonuses, equity, taxes, and benefits for confirmation with the employer.
