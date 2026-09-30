import { useCallback, useEffect, useRef, useState } from 'react'
import { api, attachmentUrl } from '../api'
import { modelShort } from '../agents'

// Files attached to a question: uploaded as soon as they're picked, dropped or pasted, sent with the question by id

export const ACCEPT = ['.png', '.jpg', '.jpeg', '.webp', '.gif', '.pdf', '.docx', '.txt', '.md', '.markdown', '.csv', '.json', '.log', '.html', '.htm']
const MAX_BYTES = 20_000_000
const MAX_FILES = 5

function extension(name) {
  const i = name.lastIndexOf('.')
  return i < 0 ? '' : name.slice(i).toLowerCase()
}

export function fileSize(bytes) {
  if (bytes < 1000) return `${bytes} B`
  if (bytes < 1_000_000) return `${Math.round(bytes / 1000)} KB`
  return `${(bytes / 1_000_000).toFixed(1)} MB`
}

// Why a file can't be attached, before it's uploaded; null when it can
export function refusal(file, count) {
  if (count >= MAX_FILES) return `At most ${MAX_FILES} files`
  if (!ACCEPT.includes(extension(file.name))) return `Can't read ${extension(file.name) || 'this kind of'} files`
  if (file.size > MAX_BYTES) return `Over ${MAX_BYTES / 1_000_000} MB`
  return null
}

export function useAttachments() {
  const [files, setFiles] = useState([]) // { key, name, kind, size, status: uploading | ready | error, id, error, preview }
  const count = useRef(0)
  const previews = useRef(new Set())
  useEffect(() => () => previews.current.forEach((u) => URL.revokeObjectURL(u)), [])

  const current = useRef([])
  useEffect(() => { current.current = files }, [files])

  const add = useCallback((list) => {
    let n = current.current.filter((f) => f.status !== 'error').length
    const added = []
    for (const file of Array.from(list || [])) {
      const key = `${Date.now()}-${count.current++}`
      const name = file.name || 'pasted image.png'
      const why = refusal({ name, size: file.size }, n)
      const image = file.type?.startsWith('image/')
      const preview = image && !why ? URL.createObjectURL(file) : null
      if (preview) previews.current.add(preview)
      added.push({ key, name, kind: image ? 'image' : 'file', size: file.size, preview, status: why ? 'error' : 'uploading', error: why })
      if (why) continue
      n += 1
      api.upload(file, name)
        .then((r) => setFiles((now) => now.map((f) => (f.key === key ? { ...f, ...r, key, preview, status: 'ready' } : f))))
        .catch((e) => setFiles((now) => now.map((f) => (f.key === key ? { ...f, status: 'error', error: e.message } : f))))
    }
    setFiles((prev) => [...prev, ...added])
  }, [])
  const remove = useCallback((key) => setFiles((prev) => prev.filter((f) => f.key !== key)), [])
  const clear = useCallback(() => setFiles([]), [])
  return {
    files,
    add,
    remove,
    clear,
    ids: files.filter((f) => f.status === 'ready').map((f) => f.id),
    uploading: files.some((f) => f.status === 'uploading'),
  }
}

// Drop files on the composer, or paste an image into it
export function dropProps(add, setOver) {
  return {
    onDragOver: (e) => { if (e.dataTransfer?.types?.includes('Files')) { e.preventDefault(); setOver(true) } },
    onDragLeave: (e) => { if (!e.currentTarget.contains(e.relatedTarget)) setOver(false) },
    onDrop: (e) => { if (e.dataTransfer?.files?.length) { e.preventDefault(); setOver(false); add(e.dataTransfer.files) } },
    onPaste: (e) => { if (e.clipboardData?.files?.length) { e.preventDefault(); add(e.clipboardData.files) } },
  }
}

export function AttachButton({ onFiles, disabled }) {
  const input = useRef(null)
  return (
    <>
      <button type="button" className="attach" onClick={() => input.current?.click()} disabled={disabled}
        aria-label="Attach an image or document" title="Attach an image, PDF or document (or drop or paste it here)">
        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M16.5 6.5 8.4 14.6a2 2 0 0 0 2.8 2.8l8.1-8.1a4 4 0 0 0-5.7-5.7l-8.1 8.1a6 6 0 0 0 8.5 8.5l6.4-6.4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>
      </button>
      <input ref={input} type="file" multiple hidden accept={ACCEPT.join(',')}
        onChange={(e) => { onFiles(e.target.files); e.target.value = '' }} />
    </>
  )
}

function FileIcon({ file }) {
  if (file.preview) return <img src={file.preview} alt="" />
  if (file.kind === 'image' && file.id && !file.error) return <img src={attachmentUrl(file.id)} alt="" />
  const ext = file.kind === 'link' ? 'LINK' : extension(file.name).slice(1, 5).toUpperCase() || 'FILE'
  return <span className="file-ext">{ext}</span>
}

export function AttachChips({ files, onRemove }) {
  if (!files.length) return null
  return (
    <div className="attach-chips" role="list" aria-label="Attached files">
      {files.map((f) => (
        <div key={f.key} role="listitem" className={`attach-chip ${f.status}`} title={f.error || f.name}>
          <FileIcon file={f} />
          <span className="attach-name">{f.name}</span>
          <span className="attach-note">
            {f.status === 'uploading' ? 'Adding…' : f.status === 'error' ? f.error
              : f.kind === 'image' ? 'image' : f.pages ? `${f.pages} page${f.pages === 1 ? '' : 's'}` : fileSize(f.size)}
          </span>
          <button type="button" onClick={() => onRemove(f.key)} aria-label={`Remove ${f.name}`}>×</button>
        </div>
      ))}
    </div>
  )
}

// On a sent message: the files, and what the council read from each (the text, or a model's description of an image)
export function MessageFiles({ files }) {
  const [open, setOpen] = useState(null)
  if (!files?.length) return null
  const shown = files.find((f) => f.id === open)
  return (
    <div className="msg-files">
      <div className="attach-chips">
        {files.map((f) => (
          <button key={f.id} type="button" className={`attach-chip sent ${f.error ? 'error' : ''} ${open === f.id ? 'on' : ''}`}
            onClick={() => setOpen(open === f.id ? null : f.id)} aria-expanded={open === f.id}>
            <FileIcon file={f} />
            <span className="attach-name">{f.name}</span>
            <span className="attach-note">{f.error ? (f.kind === 'link' ? 'couldn’t open' : 'couldn’t read') : f.read ? 'what the council read' : ['image', 'link'].includes(f.kind) ? 'reading…' : ''}</span>
          </button>
        ))}
      </div>
      {shown && (
        <div className="file-read">
          {shown.error ? <p>{shown.error}</p> : (
            <>
              <div className="file-read-by">
                {shown.kind === 'link' ? `The page at this link, as the council reads it${shown.summarized ? ' (summarized by the chair: it’s too long to include in full)' : ''}`
                  : shown.kind === 'image' ? `Described by ${shown.read_by ? modelShort(shown.read_by) : 'a model that can see'}; the council reads this, not the image`
                  : shown.summarized ? `Summarized by the chair (${modelShort(shown.read_by)}): the file is too long to include in full`
                  : 'The text the council reads'}
              </div>
              <pre>{shown.read}{shown.read && shown.chars > shown.read.length && !shown.summarized ? '\n…' : ''}</pre>
            </>
          )}
        </div>
      )}
    </div>
  )
}
