# 利用规约与采集注意事项：WTO Timeseries API

## 条款来源

- [WTO Copyright and Permissions](https://www.wto.org/english/info_e/copyrights_permissions_e.htm) 是网站一般版权和许可说明；保存文本没有表明其范围包括 Stats Portal 或 Timeseries API 数据。[读过官方页面（2026-10-24）]
- [WTO Developer Portal](https://apiportal.wto.org/) 是 API 门户页面，说明 Timeseries API 与 Key；它没有提供数据再利用或许可条款。[读过官方页面（2026-10-24）]
- 数据免责声明页面 [WTO Disclaimer](https://www.wto.org/english/info_e/disclaimer_e.htm) 在 manifest 中被记为未保存文本，且没有数据复用条款记录；本包仍将数据许可范围标为待确认。[待确认]

## 条款主题

- **复制、改编、再分发与商业用途**：网站一般版权页称其他网站材料（例如官方出版物）可在非商业用途下复制，须署名 WTO 并告知 WTO；网站材料的商业使用须取得书面许可。页面没有说明这些规则如何适用于统计数据。[读过官方页面（2026-10-24）]
- **署名要求**：一般版权说明要求署名 WTO 并告知 WTO；没有给出固定引用格式。是否适用于 Stats Portal / API 数据待确认。[读过官方页面（2026-10-24）]
- **第三方权利与例外**：保存的一般版权页和 API 门户页没有说明 Stats 数据中的第三方权利、外部来源或逐指标例外。[待确认]
- **自动化访问、批量下载、限速或封锁**：API 门户描述程序化访问，但没有给出自动化许可、批量下载规则、配额、速率、IP 限制或封锁条件。[待确认]
- **API 条款与 Key**：门户页写明「Just sign up for an API key and start consuming APIs right away. It is free.」；这说明门户所述注册和费用信息，不说明 Key 权限、限额或数据复用权利。[读过官方页面（2026-10-24）]
- **变更与可用性**：保存文本没有说明 API 或 Stats Portal 的数据更新、服务维护、版本或可用性保证。[待确认]
- **云端模型处理**：保存页面未涉及云端模型处理、第三方模型传输或模型使用。[待确认]

## 对 DATUMOJ 0.4 许可闸门的含义

- 网站一般版权说明写有「Other material such as official publications can be reproduced for non-commercial purposes provided attribution is given to the WTO and the WTO is informed.」及「Commercial use of materials from the website requires written permission from the WTO.」；这些句子不能自动映射为 Stats Portal / Timeseries API 数据规则。[读过官方页面（2026-10-24）]
- 网站一般版权文本是否适用于 Stats Portal / Timeseries API 数据未说明；数据的 outflow 与许可范围待确认。[待确认]
- 保存页面未涉及云端模型处理，cloud_model_ok 无页面依据，按计划缺省 false。[待确认]
