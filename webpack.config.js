const path = require('path');
const webpack = require('webpack');
const BundleTracker = require('webpack-bundle-tracker');
const MiniCssExtractPlugin = require('mini-css-extract-plugin');
const UglifyJsPlugin = require('uglifyjs-webpack-plugin');
const OptimizeCSSAssetsPlugin = require('optimize-css-assets-webpack-plugin');

module.exports = {
  context: __dirname,
  entry: {
    main: './assets/js/index',
    pairingDisplay: './assets/js/pairingDisplay'
  },
  optimization: {
    minimizer: [
      new UglifyJsPlugin({ cache: true, parallel: true, sourceMap: false  }),
      new OptimizeCSSAssetsPlugin({})
    ]
  },
  output: {
    path: path.resolve('./assets/webpack_bundles/'),
    filename: "[name]-[hash].js"
  },
  module: {
    rules: [{
      test: /\.css/,
      use: [
        MiniCssExtractPlugin.loader,
        "css-loader"
      ]
    }, {
      test: /\.(png|jpe?g|gif)$/,
      use: [
        {
          loader: 'file-loader',
          options: {},
        },
      ],
    }, {
      test: /\.scss$/,
      use: [
        MiniCssExtractPlugin.loader,
        "css-loader", // translates CSS into CommonJS
        "sass-loader" // compiles Sass to CSS, using Node Sass by default
      ]
    }]
  },
  resolve: {
    alias: {
      jquery: "jquery/src/jquery"
    },
    extensions: ['.js', '.scss', '.css'],
  },


  plugins: [
    new BundleTracker({filename: './webpack-stats.json'}),
    new MiniCssExtractPlugin({
      filename: '[name].css',
      chunkFilename: '[id].css'
    }),
    new webpack.ProvidePlugin({ // inject ES5 modules as global vars
      $: 'jquery',
      jQuery: 'jquery',
      'window.jQuery': 'jquery',
      Tether: 'tether'
    })
  ]
}
