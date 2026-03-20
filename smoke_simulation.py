import numpy as np
from scipy import ndimage

class SmokeSimulation:
    def __init__(self, size_x, size_y, dt=0.2, diffusion=0.1, viscosity=0.001):
        self.size_x = size_x
        self.size_y = size_y
        self.dt = dt
        self.diffusion = diffusion
        self.viscosity = viscosity
        
        # 矩阵形状为 (y, x)，匹配标准图像/网格坐标
        self.u = np.zeros((size_y, size_x))
        self.v = np.zeros((size_y, size_x))
        self.smoke = np.zeros((size_y, size_x))
        self.pressure = np.zeros((size_y, size_x))
        self.wall = np.zeros((size_y, size_x))

    def advect(self, field, u, v):
        new_field = np.zeros_like(field)
        for i in range(1, self.size_y-1):
            for j in range(1, self.size_x-1):
                if self.wall[i, j] == 1:
                    continue
                prev_i = i - self.dt * v[i, j]  # y方向速度
                prev_j = j - self.dt * u[i, j]  # x方向速度
                
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
    
    def add_boundary_conditions(self, field):
        field[0, :] = field[1, :]
        field[-1, :] = field[-2, :]
        field[:, 0] = field[:, 1]
        field[:, -1] = field[:, -2]
        return field

    def project_pressure(self, u, v):
        div = np.zeros((self.size_y, self.size_x))
        p = np.zeros((self.size_y, self.size_x))
        
        div[1:-1, 1:-1] = -0.5 * (
            (u[1:-1, 2:] - u[1:-1, :-2]) +  
            (v[2:, 1:-1] - v[:-2, 1:-1])     
        )
        div[self.wall == 1] = 0
        
        for _ in range(20): 
            p[1:-1, 1:-1] = (
                div[1:-1, 1:-1] +
                p[1:-1, :-2] + p[1:-1, 2:] +
                p[:-2, 1:-1] + p[2:, 1:-1]
            ) / 4
            p[self.wall == 1] = 0
        
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
            self.u += (dx / dist) * influence * strength
            self.v += (dy / dist) * influence * strength
            self.smoke += influence * 3.0

    def step(self, fire_origins, dt=None):
        if dt is not None:
            self.dt = dt
            
        self.inject_source(fire_origins)
        self.u, self.v = self.project_pressure(self.u, self.v)

        u_pure = self.u.copy()
        v_pure = self.v.copy()

        new_u = self.advect(u_pure, u_pure, v_pure)
        new_v = self.advect(v_pure, u_pure, v_pure)
        new_smoke = self.advect(self.smoke, u_pure, v_pure)

        self.u, self.v, self.smoke = new_u, new_v, new_smoke

        self.smoke = ndimage.gaussian_filter(self.smoke, sigma=0.6)
        self.smoke *= 0.998
        self.smoke[self.wall == 1] = 0