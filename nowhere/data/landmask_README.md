# nowwhere/data/landmask.npz 数据说明与来源

## 这是什么

`landmask.npz` 是一个位压缩的全球海陆/湖泊栅格掩膜, 是 `nowhere/coast.py`
的高精度海陆判断数据源。分辨率 1/60°(约 1.85 km/格), 文件约 1.4 MB。

- `ocean`: 1 = 海洋格
- `lakes`: 1 = 湖泊格(Natural Earth 10m lakes, 含贝加尔湖/五大湖/日内瓦湖等)
- `land`: 1 = 陆地块 (`~ocean & ~lakes`)
- `res`, `n_lat`, `n_lon`: 网格参数

## 来源与许可

- 原始数据: Natural Earth 10m 物理数据
  - `ne_10m_land` — 全球陆地多边形
  - `ne_10m_lakes` — 全球湖泊多边形
  - 下载自 https://www.naturalearthdata.com (公共领域, public domain)
- 派生过程: `tools/build_landmask.py`(Pillow 多边形栅格化, 格心采样,
  bit-pack 压缩)。派生数据不附带额外限制。

## 重建方法

```bash
pip install pillow pyshp        # 仅构建期依赖
python tools/build_landmask.py                  # 重建 1/60° 基础掩膜(逐位可复现)
python tools/build_landmask.py --res 120        # 可选 1/120°(约 0.93 km)
                                               # 保存为 landmask_hi.npz, 自动优先加载
```

构建脚本无参数运行的输出与仓库提交的 `landmask.npz` 完全一致
(受 `nowhere/tests/test_landmask_rebuild.py` 校验)。

## 已知细节

- 里海(Caspian)在 Natural Earth 中被归为 sea 而非 lake, 本掩膜沿袭该分类,
  将其判为海洋。对玩法无实质影响。
- 小于 ~2 km 的岛礁(如马尔代夫环礁)在 1/60° 掩膜中不可见; 构建 1/120°
  掩膜或依赖网格兜底可缓解。
- 掩膜不存在时 `coast.py` 自动降级到 1° `grid_tiny.npz`, 结果带
  `source="grid1", confidence=0.5` 标记。
