import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'
import { LEGACY_COLOR_LIMITS } from './scripts/color-gate-manifest.mjs'

// 颜色令牌门禁（DESIGN.md §2.4/§8）：存量硬编码颜色文件从下方 hex/rgba
// 约束豁免，豁免名单与 check:color-tokens 共用同一份棘轮清单，
// 存量迁移后收紧 manifest 即自动同步，无需改本文件。
const legacyColorTsFiles = Object.keys(LEGACY_COLOR_LIMITS)
  .filter(rel => /\.tsx?$/.test(rel))
  .map(rel => `src/${rel}`)
const clipboardRestrictedSelector = {
  selector: "MemberExpression[object.name='navigator'][property.name='clipboard']",
  message: 'Use writeTextToClipboard from @/utils/clipboard so copying also works on HTTP deployments.',
}
const colorTokenSelectors = [
  {
    selector: 'Literal[value=/#[0-9a-fA-F]{3,4}|#[0-9a-fA-F]{6}|#[0-9a-fA-F]{8}/]',
    message: '界面颜色必须来自 tokens.css 语义 token（Tailwind 语义类或 var(--token)）；图表序列 import @/lib/echartsTheme。存量文件见 scripts/color-gate-manifest.mjs（npm run check:color-tokens）。',
  },
  {
    selector: 'Literal[value=/rgba?\\(|hsla?\\(/]',
    message: '界面颜色必须来自 tokens.css 语义 token（Tailwind 语义类或 var(--token)）；图表序列 import @/lib/echartsTheme。存量文件见 scripts/color-gate-manifest.mjs（npm run check:color-tokens）。',
  },
]

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
    },
    rules: {
      // The API and ontology editors intentionally operate on schemaless JSON
      // supplied by users and external services. Tightening those boundaries is
      // a separate runtime-validation project; changing hundreds of these types
      // during a lint-only cleanup would create false safety and regression risk.
      '@typescript-eslint/no-explicit-any': 'off',
      '@typescript-eslint/no-unused-vars': ['error', {
        argsIgnorePattern: '^_',
        caughtErrorsIgnorePattern: '^_',
        varsIgnorePattern: '^_',
      }],

      // These React Compiler diagnostics reject established, valid React
      // patterns in this non-compiler application (for example, loading data or
      // resetting a form when a modal opens). Keep the correctness-oriented
      // Rules of Hooks checks enabled while excluding compiler-only guidance.
      'react-hooks/error-boundaries': 'off',
      'react-hooks/immutability': 'off',
      'react-hooks/incompatible-library': 'off',
      'react-hooks/preserve-manual-memoization': 'off',
      'react-hooks/purity': 'off',
      'react-hooks/refs': 'off',
      'react-hooks/set-state-in-effect': 'off',

      // Existing effects deliberately use snapshot semantics in several editor
      // flows. Rewriting their dependency arrays can trigger extra requests or
      // reset user input, which is outside a behavior-preserving lint cleanup.
      'react-hooks/exhaustive-deps': 'off',

      // Co-locating variants and small helpers with their components is part of
      // the public component API. This only affects development-time HMR.
      'react-refresh/only-export-components': 'off',
    },
  },
  {
    files: ['src/**/*.{ts,tsx}'],
    ignores: ['src/utils/clipboard.ts', 'src/test/**'],
    rules: {
      'no-restricted-syntax': ['error', clipboardRestrictedSelector],
    },
  },
  // 存量硬编码颜色文件：只保留剪贴板约束，颜色约束待迁移完成后由
  // manifest 移除登记时自动纳入下方全局块。
  {
    files: legacyColorTsFiles,
    rules: {
      'no-restricted-syntax': ['error', clipboardRestrictedSelector],
    },
  },
  {
    files: ['src/**/*.{ts,tsx}'],
    ignores: ['src/utils/clipboard.ts', 'src/test/**', 'src/lib/echartsTheme.ts', ...legacyColorTsFiles],
    rules: {
      'no-restricted-syntax': ['error', clipboardRestrictedSelector, ...colorTokenSelectors],
    },
  },
])
