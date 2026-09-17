文件：操场右
初始状态（0-20秒左右静止）

![image-20260915102813599](D:\Code\radar\文档\ceishi.assets\image-20260915102813599.png)

转动雷达状态（20秒-1分20秒）

![image-20260915103048815](D:\Code\radar\文档\ceishi.assets\image-20260915103048815.png)

![image-20260915102914644](D:\Code\radar\文档\ceishi.assets\image-20260915102914644.png)

转动完毕（1分20秒到结束）
![image-20260915103021801](D:\Code\radar\文档\ceishi.assets\image-20260915103021801.png)

---

文件：操场左

初始状态（0秒到20秒）![image-20260915103640735](D:\Code\radar\文档\ceishi.assets\image-20260915103640735.png)

移动状态（20秒到1分50秒）

![image-20260915103726607](D:\Code\radar\文档\ceishi.assets\image-20260915103726607.png)

结束：（1：53-2：02）

![image-20260915103929077](D:\Code\radar\文档\ceishi.assets\image-20260915103929077.png)



所以我发现移动的过程中球门啊和外界高楼围墙这些静止物体是会重叠起来的，应为雷达是非重复扫描雷达，而我现在需要构建出操场我完整的地图：所以我现在需要开发一个算法解决这个问题