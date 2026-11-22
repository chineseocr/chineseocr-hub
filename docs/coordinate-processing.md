# 坐标处理 / Coordinate mapping

本文说明文字块、字段和表格位置如何从模型坐标映射到来源图像。页面操作见 [表格与来源核对](tables-and-localization.md)。

## 坐标字段

| 字段 | 含义 |
|---|---|
| `model_box` | 模型返回的 `[x1, y1, x2, y2]`，范围 0–1000 |
| `inference_width` / `inference_height` | 实际提交模型的图像尺寸 |
| `box` / `polygon` | 用于来源预览叠加的像素矩形 / 四角多边形 |
| `source_box` / `source_polygon` | 映射到原图像素或 PDF points 的位置；按结果契约提供 |
| `position_source` | 位置来源，例如模型直接返回、上下文定位或图像线框辅助 |

图片原图尺寸以 EXIF 方向归一化后的画布为准。PDF 页面尺寸使用 points；浏览器缩放不改变上述数据坐标。

## 转换顺序

1. 上传图片时归一化 EXIF；为图片和 PDF 生成最长边不超过 1600px 的预览，并记录来源尺寸。
2. 按运行配置执行顺时针旋转及可选小角度纠偏，记录实际推理尺寸和逆变换矩阵。多文档实例使用裁剪后的画布。
3. 将模型坐标还原到实际推理图像素：

   ```text
   x_pixel = x_model / 1000 * inference_width
   y_pixel = y_model / 1000 * inference_height
   ```

4. 将框的四个角依次逆纠偏、逆旋转；裁剪实例加回全页偏移，得到预览坐标。
5. 按预览与来源尺寸比例，分别映射到原图像素或 PDF points。
6. 保留文字坐标的小数；仅执行像素裁剪时对边界向外取整。

例如实际推理图为 425×600、原图为 4250×6000，模型框为 `[100.13, 200.27, 600.49, 800.31]`。没有旋转或裁剪时，原图框为 `[425.5525, 1201.62, 2552.0825, 4801.86]`。

## 位置来源与缺失值

- `model_reported`：模型直接返回。
- `model_contextual`：结合标签、文字和上下文再次定位。
- `model_band_intersection`：由模型返回的行带与列带交集确定。
- `image_grid_cell`：清晰线框表格的图像几何辅助结果。
- `unavailable`：未获得有效位置，坐标为 `null`。

矩形用于范围选择，多边形保留旋转后的四角。`confidence` 为 `null`；位置来源不作为概率分数。历史结果按其保存的坐标元数据解释，不自动重新推理。

## 相关实现

- `workbench/geometry.py`：旋转、投影纠偏、逆矩阵和四角映射。
- `workbench/vision.py`：归一化框校验、实例裁剪及来源尺寸换算。
- `workbench/document_analysis.py`：全文、表格及上下文定位的几何元数据。
- `workbench/structured_analysis.py`：业务字段与联合 PDF 的逐页定位。

## English

Model boxes use normalized 0–1000 coordinates. Convert using the actual inference width and height, inverse-map all four corners through deskew and rotation, add crop offsets, then scale to the oriented source-image pixels or PDF points. Preserve fractional text positions; round outwards only for raster crops. Missing positions remain null. Position-source metadata distinguishes direct, contextual, row/column-band and pixel-grid geometry.
