import numpy as np

class SmokeSimulation:
    def __init__(self, size_x, size_y, dt=0.2, diffusion=0.1, viscosity=0.0001, strength=0.5, inismoke=0.01, decay_factor=0.9996, dx=0.5):
        self.size_x = size_x
        self.size_y = size_y
        self.dt = dt
        self.dx = dx
        self.diffusion = diffusion  # 烟雾扩散率
        self.viscosity = viscosity  # 速度场粘滞系数
        self.strength = strength
        self.inismoke = inismoke
        self.decay_factor = decay_factor
        
        self.u = np.zeros((size_y, size_x))
        self.v = np.zeros((size_y, size_x))
        self.smoke = np.zeros((size_y, size_x))
        self.pressure = np.zeros((size_y, size_x))
        self.wall = np.zeros((size_y, size_x))

    def add_boundary_conditions(self, field):
        field[0, :] = field[1, :]
        field[-1, :] = field[-2, :]
        field[:, 0] = field[:, 1]
        field[:, -1] = field[:, -2]
        return field

    def _neumann_neighbors(self, field):
        """Return (left, right, up, down) with Neumann BC: wall neighbors replaced by center value."""
        center = field[1:-1, 1:-1]
        wall_mask = self.wall == 1

        left_raw = field[1:-1, :-2]
        left_nbr = np.where(wall_mask[1:-1, :-2], center, left_raw)

        right_raw = field[1:-1, 2:]
        right_nbr = np.where(wall_mask[1:-1, 2:], center, right_raw)

        up_raw = field[:-2, 1:-1]
        up_nbr = np.where(wall_mask[:-2, 1:-1], center, up_raw)

        down_raw = field[2:, 1:-1]
        down_nbr = np.where(wall_mask[2:, 1:-1], center, down_raw)

        return left_nbr, right_nbr, up_nbr, down_nbr

    def diffuse(self, field, diff_rate, iterations=20):
        if diff_rate <= 0:
            return field
        
        # 这里的 a 对应于扩散强度因子
        a = (self.dt * diff_rate) / (self.dx ** 2)
        new_field = field.copy()
        
        for _ in range(iterations):
            left, right, up, down = self._neumann_neighbors(new_field)

            new_field[1:-1, 1:-1] = (
                field[1:-1, 1:-1] + a * (left + right + up + down)
            ) / (1 + 4 * a)

            new_field[self.wall == 1] = 0
            new_field = self.add_boundary_conditions(new_field)
            
        return new_field

    def advect(self, field, u, v):
        new_field = np.zeros_like(field)

        # 构造内部网格索引矩阵 (shape: (size_y-2, size_x-2))
        i_idx, j_idx = np.meshgrid(
            np.arange(1, self.size_y - 1),
            np.arange(1, self.size_x - 1),
            indexing='ij'
        )

        # 半拉格朗日回溯（全网格同时计算）
        prev_i = i_idx - (self.dt * v[1:-1, 1:-1]) / self.dx
        prev_j = j_idx - (self.dt * u[1:-1, 1:-1]) / self.dx

        # Clamp 到有效范围
        prev_i = np.clip(prev_i, 0.5, self.size_y - 1.5)
        prev_j = np.clip(prev_j, 0.5, self.size_x - 1.5)

        # 整数部分 & 小数部分
        i0 = np.floor(prev_i).astype(np.int64)
        j0 = np.floor(prev_j).astype(np.int64)
        i1 = i0 + 1
        j1 = j0 + 1

        s1 = prev_i - i0
        t1 = prev_j - j0
        s0 = 1.0 - s1
        t0 = 1.0 - t1

        # 双线性插值（fancy indexing，C 层完成）
        new_field[1:-1, 1:-1] = (
            s0 * (t0 * field[i0, j0] + t1 * field[i0, j1]) +
            s1 * (t0 * field[i1, j0] + t1 * field[i1, j1])
        )

        new_field[self.wall == 1] = 0
        return self.add_boundary_conditions(new_field)

    def project_pressure(self, u, v, iterations=20):
        div = np.zeros((self.size_y, self.size_x))
        p = np.zeros((self.size_y, self.size_x))
        
        div[1:-1, 1:-1] = -0.5 *self.dx * (
            (u[1:-1, 2:] - u[1:-1, :-2]) +  
            (v[2:, 1:-1] - v[:-2, 1:-1])     
        )
        div[self.wall == 1] = 0
        
        for _ in range(iterations): 
            p[1:-1, 1:-1] = (
                div[1:-1, 1:-1] +
                p[1:-1, :-2] + p[1:-1, 2:] +
                p[:-2, 1:-1] + p[2:, 1:-1]
            ) / 4
            p[self.wall == 1] = 0
            p = self.add_boundary_conditions(p)
        
        u[1:-1, 1:-1] -= 0.5 * (p[1:-1, 2:] - p[1:-1, :-2])/self.dx
        v[1:-1, 1:-1] -= 0.5 * (p[2:, 1:-1] - p[:-2, 1:-1])/self.dx
        
        u = self.add_boundary_conditions(u)
        v = self.add_boundary_conditions(v)
        u[self.wall == 1] = 0 
        v[self.wall == 1] = 0 
        return u, v

    def inject_source(self, fire_origins,strength = 0.5,inismoke = 0.01):
        if not fire_origins:
            return
        
        y, x = np.indices((self.size_y, self.size_x))
        sigma = 1.5

        
        for fx, fy in fire_origins:
            dx = x - fx
            dy = y - fy
            dist_sq = dx**2 + dy**2
            dist = np.sqrt(dist_sq) + 1e-9
            
            influence = np.exp(-dist_sq / (2 * sigma**2))
            self.u += (dx / dist) * influence * strength * (self.dt / 0.2)
            self.v += (dy / dist) * influence * strength * (self.dt / 0.2)
            self.smoke += influence * (inismoke * self.dt)
    
    def apply_blur(self, field, amount=0.2):

        if amount <= 0:
            return field
        
        smoothed = field.copy()
        smoothed[1:-1, 1:-1] = (1 - amount) * field[1:-1, 1:-1] + (amount / 4.0) * (
            field[1:-1, :-2] + field[1:-1, 2:] +
            field[:-2, 1:-1] + field[2:, 1:-1]
        )
        
        smoothed[self.wall == 1] = 0
        return self.add_boundary_conditions(smoothed)

    def step(self, fire_origins, dt=None,
             diffusion=None, viscosity=None,
             strength=None, inismoke=None,
             decay_factor=None, dx=None):
        if dt is not None:
            self.dt = dt
        if diffusion is not None:
            self.diffusion = diffusion
        if viscosity is not None:
            self.viscosity = viscosity
        if strength is not None:
            self.strength = strength
        if inismoke is not None:
            self.inismoke = inismoke
        if decay_factor is not None:
            self.decay_factor = decay_factor
        if dx is not None:
            self.dx = dx
            
        # 1. 外部力/源注入
        self.inject_source(fire_origins,self.strength, self.inismoke)

        # 2. 扩散步骤 (Diffusion)
        # 速度场的扩散即为粘滞力 (Viscosity)
        self.u = self.diffuse(self.u, self.viscosity)
        self.v = self.diffuse(self.v, self.viscosity)
        # 烟雾浓度的扩散
        self.smoke = self.diffuse(self.smoke, self.diffusion)

        # 3. 压力投影 (保持不可压缩性)
        self.u, self.v = self.project_pressure(self.u, self.v)

        # 4. 对流步骤 (Advection)
        u_pure = self.u.copy()
        v_pure = self.v.copy()

        self.u = self.advect(u_pure, u_pure, v_pure)
        self.v = self.advect(v_pure, u_pure, v_pure)
        self.smoke = self.advect(self.smoke, u_pure, v_pure)

        actual_factor = self.decay_factor ** (self.dt / 0.2)
        self.smoke *= actual_factor
        # self.smoke = self.apply_blur(self.smoke, amount=0.4)
        self.smoke[self.wall == 1] = 0
