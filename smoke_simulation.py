import numpy as np

class SmokeSimulation:
    def __init__(self, size_x, size_y, dt=0.2, diffusion=0.001, viscosity=0.0001):
        self.size_x = size_x
        self.size_y = size_y
        self.dt = dt
        self.diffusion = diffusion  # 烟雾扩散率
        self.viscosity = viscosity  # 速度场粘滞系数
        
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

    def diffuse(self, field, diff_rate, iterations=20):
        """
        物理扩散步骤：使用 Gauss-Seidel / Jacobi 迭代求解隐式扩散方程
        """
        if diff_rate <= 0:
            return field
        
        # 这里的 a 对应于扩散强度因子
        a = self.dt * diff_rate * (self.size_x - 2) * (self.size_y - 2)
        new_field = field.copy()
        
        for _ in range(iterations):
            new_field[1:-1, 1:-1] = (
                field[1:-1, 1:-1] + a * (
                    new_field[1:-1, :-2] + new_field[1:-1, 2:] +
                    new_field[:-2, 1:-1] + new_field[2:, 1:-1]
                )
            ) / (1 + 4 * a)
            # 保持边界和墙壁处理
            new_field[self.wall == 1] = 0
            new_field = self.add_boundary_conditions(new_field)
            
        return new_field

    def advect(self, field, u, v):
        new_field = np.zeros_like(field)
        for i in range(1, self.size_y-1):
            for j in range(1, self.size_x-1):
                if self.wall[i, j] == 1:
                    continue
                # 半拉格朗日回溯
                prev_i = i - self.dt * v[i, j]
                prev_j = j - self.dt * u[i, j]
                
                prev_i = max(0.5, min(self.size_y-1.5, prev_i))
                prev_j = max(0.5, min(self.size_x-1.5, prev_j))
                
                i0, j0 = int(prev_i), int(prev_j)
                i1, j1 = i0 + 1, j0 + 1
                s1, t1 = prev_i - i0, prev_j - j0
                s0, t0 = 1 - s1, 1 - t1
                
                new_field[i, j] = (
                    s0 * (t0 * field[i0, j0] + t1 * field[i0, j1]) +
                    s1 * (t0 * field[i1, j0] + t1 * field[i1, j1])
                )
        return self.add_boundary_conditions(new_field)

    def project_pressure(self, u, v, iterations=20):
        div = np.zeros((self.size_y, self.size_x))
        p = np.zeros((self.size_y, self.size_x))
        
        div[1:-1, 1:-1] = -0.5 * (
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
        
        u[1:-1, 1:-1] -= 0.5 * (p[1:-1, 2:] - p[1:-1, :-2])
        v[1:-1, 1:-1] -= 0.5 * (p[2:, 1:-1] - p[:-2, 1:-1])
        
        u = self.add_boundary_conditions(u)
        v = self.add_boundary_conditions(v)
        u[self.wall == 1] = 0 
        v[self.wall == 1] = 0 
        return u, v

    def inject_source(self, fire_origins):
        if not fire_origins:
            return
        
        y, x = np.indices((self.size_y, self.size_x))
        sigma = 1.5
        strength = 0.5
        
        for fx, fy in fire_origins:
            dx = x - fx
            dy = y - fy
            dist_sq = dx**2 + dy**2
            dist = np.sqrt(dist_sq) + 1e-9
            
            influence = np.exp(-dist_sq / (2 * sigma**2))
            self.u += (dx / dist) * influence * strength * (self.dt / 0.2)
            self.v += (dy / dist) * influence * strength * (self.dt / 0.2)
            self.smoke += influence * (15.0 * self.dt)

    def step(self, fire_origins, dt=None):
        if dt is not None:
            self.dt = dt
            
        # 1. 外部力/源注入
        self.inject_source(fire_origins)

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

        decay_factor = 1 ** (self.dt / 0.2)
        self.smoke *= decay_factor
        self.smoke[self.wall == 1] = 0