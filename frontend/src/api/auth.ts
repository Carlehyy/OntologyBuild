import { apiClient } from './client'
import type { User } from '@/types/auth'

export interface UserEnvVar {
  key: string
  value: string
}

export const authApi = {
  login: (username: string, password: string) =>
    apiClient.post<{ access_token: string; token_type: string }>('/auth/login', { username, password }),
  register: (username: string, email: string, password: string) =>
    apiClient.post<User>('/auth/register', { username, email, password }),
  profile: () => apiClient.get<User>('/auth/profile'),
  changePassword: (current_password: string, new_password: string) =>
    apiClient.put('/auth/password', { current_password, new_password }),
  // 个人资料自助更新（MYW-56）：用户名不可自改，仅邮箱
  updateProfile: (email: string) => apiClient.put<User>('/auth/profile', { email }),
  // 用户私有环境变量：GET 列表 / PUT 全量保存（key/value 均为字符串）
  listEnvVars: () => apiClient.get<UserEnvVar[]>('/auth/env-vars'),
  saveEnvVars: (items: UserEnvVar[]) =>
    apiClient.put<UserEnvVar[]>('/auth/env-vars', { items }),
}
