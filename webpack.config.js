var path = require('path');
var webpack = require('webpack');
var BundleTracker = require('webpack-bundle-tracker');

module.exports = {
  context: __dirname,
  entry: './assets/js/index',
  output: {
    path: path.resolve('./assets/webpack_bundles/'),
    filename: "[name]-[hash].js"
  },
  module: {
    rules: [{
      test: /\.css/,
      use: [
        "style-loader",
        "css-loader"
      ]
    }, {
      test: /\.scss$/,
      use: [
        "style-loader", // creates style nodes from JS strings
        "css-loader", // translates CSS into CommonJS
        "sass-loader" // compiles Sass to CSS, using Node Sass by default
      ]
    }]
  },
  resolve: {
    extensions: ['.js', '.scss', '.css'],
  },


  plugins: [
    new BundleTracker({filename: './webpack-stats.json'})
  ]
}
