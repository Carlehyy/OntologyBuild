export interface User {
  id: string
  username: string
  email: string
  role: 'admin' | 'editor' | 'viewer' | 'custom'
  is_active: boolean
  created_at: string
  menu_permissions?: string[]
}

export interface TokenResponse {
  access_token: string
  token_type: string
}
