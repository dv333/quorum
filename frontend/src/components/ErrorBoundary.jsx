import { Component } from 'react'

// One broken message shouldn't blank the whole app: show a way back instead.
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidUpdate(prev) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null })
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="empty-state">
        <h2>Something went wrong showing this</h2>
        <p className="muted">{String(this.state.error.message || this.state.error)}</p>
        <button className="btn blue" onClick={() => this.setState({ error: null })}>Try again</button>
      </div>
    )
  }
}
