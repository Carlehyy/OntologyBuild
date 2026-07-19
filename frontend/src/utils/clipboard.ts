export async function writeTextToClipboard(value: string): Promise<void> {
  let clipboardError: unknown

  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value)
      return
    } catch (error) {
      clipboardError = error
      // Clipboard API is unavailable on ordinary HTTP deployments; fall back below.
    }
  }

  const activeElement = document.activeElement instanceof HTMLElement ? document.activeElement : null
  const selection = window.getSelection()
  const selectedRanges = selection
    ? Array.from({ length: selection.rangeCount }, (_, index) => selection.getRangeAt(index).cloneRange())
    : []
  const textarea = document.createElement('textarea')
  textarea.value = value
  textarea.setAttribute('readonly', '')
  textarea.setAttribute('aria-hidden', 'true')
  Object.assign(textarea.style, {
    position: 'fixed',
    top: '0',
    left: '-9999px',
    opacity: '0',
    pointerEvents: 'none',
  })
  document.body.appendChild(textarea)

  let copied: boolean
  try {
    textarea.focus({ preventScroll: true })
    textarea.select()
    textarea.setSelectionRange(0, value.length)
    copied = document.execCommand('copy')
  } finally {
    textarea.remove()
    if (selection) {
      selection.removeAllRanges()
      selectedRanges.forEach(range => selection.addRange(range))
    }
    activeElement?.focus({ preventScroll: true })
  }

  if (!copied) {
    throw clipboardError instanceof Error
      ? clipboardError
      : new Error('Clipboard copy was rejected')
  }
}
