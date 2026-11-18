# Ch09 飞轮评测 · after

> 生成时间：2026-11-18T14:49:46 ｜ fixture：`/Users/lidong/Desktop/workFile/ecom-agent/data/eval/flywheel_badcases.yaml` ｜ dataset_hash=`fca8f2d1fd4b` ｜ kb_version=`865da1a0b66e`
> 样本：badcases=12，out_of_kb=10

## 汇总

| 指标 | 值 |
|---|---:|
| hit@3 | 1.000 |
| recall | 0.958 |
| citation_hit | 1.000 |
| refusal_rate | 0.800 |

## Badcases

| id | route | hit@3 | recall | citation | answer |
|---|---|---|---:|---|---|
| F01 | knowledge | True | 1.00 | True | 价保规则：签收后 7 天内同款商品降价可申请补差，具体以活动规则为准[1]。 |
| F02 | knowledge | True | 1.00 | True | 目前不支持以旧换新，旧商品需要您自行处理[1]。 |
| F03 | knowledge | True | 1.00 | True | 发货前可以联系客服修改收货地址；已发货后无法直接修改，可以拒收后重新下单[1]。已发货订单确实需要改地址的，请转人工处理[2]。 |
| F04 | knowledge | True | 1.00 | True | 不支持指定快递，仓库会按地区和商品自动选择快递[1]。 |
| F05 | knowledge | True | 1.00 | True | 目前不支持货到付款，下单时需在线支付[1]。 |
| F06 | knowledge | True | 1.00 | True | 目前没有线下门店试坐服务；推车参数可以在商品页查看，或联系客服咨询[1]。 |
| F07 | knowledge | True | 0.50 | True | 纸质发票寄丢了，需要联系客服核实；如果要重开纸质发票，需要把原件寄回，按税务要求处理[1]。 |
| F08 | knowledge | True | 1.00 | True | 运费险理赔审核通过后一般 3–5 个工作日到账，单笔上限 12 元[1]。 |
| F09 | knowledge | True | 1.00 | True | 预售商品定金能否退还，以商品页面的规则为准；如果还没付尾款，可以联系客服处理[1]。 |
| F10 | knowledge | True | 1.00 | True | 生日券没收到的话，请先确认一下你的会员等级和生日月份，然后联系客服核实后补发[1]。不过补发需要人工处理，我这边没法直接发放或补发优惠券[3]。 |
| F11 | knowledge | True | 1.00 | True | 订单支持拆单发货，具体以仓库库存和商品来源为准[1]。 |
| F12 | knowledge | True | 1.00 | True | 大件商品配送支持上楼，偏远地区或特殊小区以配送员沟通为准[1]。 |

## Out-of-KB

| id | route | refused | answer |
|---|---|---|---|
| O01 | data | False | 请提供商品型号（例如 ST-20），我帮您查库存。 |
| O02 | fallback | True | 抱歉，这个问题我暂时没能理解。您可以换个说法，或者点「转人工」由客服跟进。 |
| O03 | knowledge | True | 关于「支持花呗分期吗」，我这边没有找到确切的依据，不能给您结论。已经为您记录，您是不是想问「是否支持货到付款」？ |
| O04 | fallback | True | 抱歉，这个问题我暂时没能理解。您可以换个说法，或者点「转人工」由客服跟进。 |
| O05 | fallback | True | 抱歉，这个问题我暂时没能理解。您可以换个说法，或者点「转人工」由客服跟进。 |
| O06 | knowledge | True | 关于「可以定制婴儿床吗」，我这边没有找到确切的依据，不能给您结论。已经为您记录，您是不是想问「型号交叉点与选购提示」？ |
| O07 | data | False | 请提供商品型号（例如 ST-20），我帮您查库存。 |
| O08 | knowledge | True | 没有找到关于跨境电商直邮的说明。资料里只提到港澳台及海外地区不发货[3][5]，所以跨境直邮这块目前是不支持的。 |
| O09 | fallback | True | 抱歉，这个问题我暂时没能理解。您可以换个说法，或者点「转人工」由客服跟进。 |
| O10 | fallback | True | 抱歉，这个问题我暂时没能理解。您可以换个说法，或者点「转人工」由客服跟进。 |
