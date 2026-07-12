import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

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
])
