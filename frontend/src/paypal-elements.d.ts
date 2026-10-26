// PayPal JS SDK v6 registers <paypal-button> as a native web component.
import 'react'

declare module 'react' {
  namespace JSX {
    interface IntrinsicElements {
      'paypal-button': React.DetailedHTMLProps<React.HTMLAttributes<HTMLElement>, HTMLElement> & {
        type?: 'pay' | 'checkout' | 'buynow' | 'subscribe'
      }
    }
  }
}
