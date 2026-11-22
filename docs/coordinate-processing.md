# 坐标处理与定位质量 / Coordinates and localization quality

**v0.6 更新：** 现已接入小角度投影纠偏、四角仿射逆映射、上下文再定位和模型行列锚点；使用方式及实际边界见 [当前指南](tables-and-localization.md)。以下比较和提示词试验保留为 v0.5 的历史调查记录，不代表 v0.6 能力状态。

2026-11-22。对照本地医疗票据项目的预处理、位置归一化、行列定位、逆变换和输出流程后，确认需要把**几何映射正确性**与**模型定位质量**分别验证。没有复制其业务代码、专用提示词、样本或模型权重进入本仓库。

## 当前工作台的转换顺序

1. 图片先做 EXIF 方向归一化。这里的原图坐标指方向归一化后的画布；不是 JPEG 文件未经旋转的存储像素。
2. 上传页生成最长边不超过 1600 的预览图，保留原图尺寸。PDF 保留页面 points 尺寸及实际渲染图像素尺寸。
3. 如使用显式旋转，模型收到旋转后的预览图或文档裁剪图。`inference_width/height` 是这一步的实际图像宽高，不是浏览器显示尺寸。
4. 模型的 `[x1,y1,x2,y2]` 保留为 `model_box`。先分别用 `x / 1000 × inference_width`、`y / 1000 × inference_height` 还原推理图像素。
5. 对四个角逐一执行旋转逆变换，再取包围框。裁剪 OCR 的结果需要加回裁剪在全页中的偏移。
6. `box` 用于预览图叠加；`source_box` 按各轴尺寸比例映射到原图像素或 PDF points。文字框保留小数到最终输出；实际裁剪为了包含边缘像素才向外取整。

例如原图 4250×6000、预览 425×600，模型框 `[100.13,200.27,600.49,800.31]`：没有旋转时原图坐标约为 `[425.5525,1201.62,2552.0825,4801.86]`。若先将预览框取整再乘 10，会引入数个原图像素的额外误差。

独立 `recognize()` 字节接口同样遵循 EXIF → 显式旋转 → 逆变换；输出 `width/height` 属于方向归一化的来源画布。裁剪实例中的推理宽高则属于裁剪图，不能直接拿全页尺寸解释其 `model_box`。

旧任务不回写新字段；已有模型框不会因为更新代码而自动重新定位或修复。

## 与医疗票据流程的差异

| 环节 | 医疗票据参考流程 | 当前工作台 |
|---|---|---|
| 原图与识别图 | 分离记录，保存变换信息 | 保留预览/原图尺寸、推理尺寸及归一化框 |
| 旋转、纠偏 | 方向检测、倾斜矫正，仿射逆矩阵回原图 | EXIF 和手动 90/180/270 度；未做自动倾斜或透视矫正 |
| 文字/字段定位 | 直接 location，必要时上下文二次定位 | 全文文字块直接定位；业务路由保留文档实例框 |
| 表格定位 | 行带/列带/锚点组合，专项质量检查 | 尚无行列锚点和单元格几何重建 |
| 输出校验 | 页码、框、匹配文本与业务位置检查 | 框类型、边界和次序检查；匹配文本及业务位置质量检查尚未实现 |

当前不存在纠偏变换，因此也没有遗漏一张已纠偏图的仿射逆矩阵。未来增加纠偏时必须同时增加正/逆矩阵、四角 polygon、预览映射和回归测试，不能只旋转输入图。

## 合成样本观测与限制

本轮仅向用户已授权的多模态服务提交从零生成的 425×600 合成证书，不提交用户证件或医疗数据。该样本含八条文字，使用绘制时记录的文字包围框比较模型结果。

- 先前当前默认契约观测：八条匹配文字的平均 IoU 约 0.184。
- 本轮采用医疗项目风格的中文逐行 `location/bbox/matched_text` 契约试验：八条匹配文字，平均 IoU 约 0.013，没有改善。该试验是通用改写，不是原医疗流水线的端到端评测。
- 同样本的独立上下文定位试验返回非法 JSON，未获得可比较定位结果。

这些是单样本、不同请求的观测，不是受控业务精度基准，也不能据此否定医疗专用流程。没有将试验提示词替换为默认提示词，没有自动增加付费定位调用或用推算框掩盖模型错误。原图换算小数精度和 EXIF 修正只消除确定的工程误差；实际位置仍需要代表性标注集验证。

## English

Keep raw model boxes in normalized 0..1000 coordinates. Convert using the actual inference canvas dimensions, invert rotation at all four corners, add crop offsets, and only then map to the oriented source-image pixels or PDF points. Preserve fractional text coordinates; outward integer rounding is only for raster crops. Standalone OCR normalizes EXIF orientation as uploaded pages do.

The medical reference also has deskew matrices, contextual relocalization and table-band logic. These are not implemented here. Two synthetic prompt-style experiments did not establish an improvement, so the default prompt and call count are unchanged. Geometry tests do not certify model localization accuracy; historical tasks are not rewritten.
